"""Ties the write-ahead log, memtable, on-disk segments, and compaction
together into one log-structured-merge-tree key/value storage engine --
the same family of design (WAL + memtable + immutable sorted segments +
background compaction) that WiredTiger-style and RocksDB/LevelDB-style
storage engines use under a database's query layer. document.py builds a
small MongoDB-shaped document API on top of this.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

from .memtable import MISSING, MemTable, TOMBSTONE, Value
from .sstable import SSTableReader, SSTableWriter
from .wal import Op, WriteAheadLog

# Deliberately small so tests (and a quick demo) can trigger a real flush
# without needing to write megabytes of data first.
DEFAULT_MEMTABLE_FLUSH_BYTES = 64 * 1024


class LSMEngine:
    def __init__(self, directory: Union[str, Path], *, flush_threshold_bytes: int = DEFAULT_MEMTABLE_FLUSH_BYTES):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.flush_threshold_bytes = flush_threshold_bytes
        self._lock = threading.RLock()

        self._wal = WriteAheadLog(self.directory / "wal.log")
        self._memtable = MemTable()

        self._segment_paths: List[Path] = self._discover_segments()
        self._readers: List[SSTableReader] = [SSTableReader(p) for p in self._segment_paths]
        self._next_segment_id = self._infer_next_segment_id()

        self._recover_from_wal()

    # -- bookkeeping -----------------------------------------------------

    def _discover_segments(self) -> List[Path]:
        return sorted(self.directory.glob("segment-*.sst"), key=lambda p: int(p.stem.split("-")[1]))

    def _infer_next_segment_id(self) -> int:
        if not self._segment_paths:
            return 0
        return max(int(p.stem.split("-")[1]) for p in self._segment_paths) + 1

    def _recover_from_wal(self) -> None:
        """Re-applies every record in the write-ahead log to the (empty)
        memtable. Called on startup so a process that crashed after
        acknowledging a write but before flushing comes back with exactly
        the writes it had acknowledged, and none it hadn't."""
        for op, key, value in self._wal.replay():
            if op is Op.PUT:
                assert value is not None
                self._memtable.put(key, value)
            else:
                self._memtable.delete(key)

    # -- public KV API -----------------------------------------------------

    def put(self, key: bytes, value: bytes) -> None:
        with self._lock:
            self._wal.append(Op.PUT, key, value)
            self._memtable.put(key, value)
            self._maybe_flush()

    def delete(self, key: bytes) -> None:
        with self._lock:
            self._wal.append(Op.DELETE, key, None)
            self._memtable.delete(key)
            self._maybe_flush()

    def get(self, key: bytes) -> Optional[bytes]:
        with self._lock:
            mem_val = self._memtable.get(key)
            if mem_val is not MISSING:
                return None if mem_val is TOMBSTONE else mem_val  # type: ignore[return-value]

            # Newest segment first: a key can appear in several segments if
            # it was overwritten across flushes, and the newest write wins.
            for reader in reversed(self._readers):
                val = reader.get(key)
                if val is not MISSING:
                    return None if val is TOMBSTONE else val  # type: ignore[return-value]
            return None

    def scan(self, start: Optional[bytes] = None, end: Optional[bytes] = None) -> Iterator[Tuple[bytes, bytes]]:
        """Yields (key, value) for start <= key < end in ascending key
        order, merging the memtable and every on-disk segment and keeping
        only the newest version of each key (tombstoned keys are skipped).
        """
        with self._lock:
            candidates: Dict[bytes, Value] = {}
            # Oldest segment first, then the memtable last, so each later
            # write simply overwrites the dict entry -- the last one
            # written for a given key is the one that survives.
            for reader in self._readers:
                for k, v in reader.scan(start, end):
                    candidates[k] = v
            for k, v in self._memtable.items_sorted():
                if (start is None or k >= start) and (end is None or k < end):
                    candidates[k] = v

            for k in sorted(candidates.keys()):
                v = candidates[k]
                if v is not TOMBSTONE:
                    yield k, v  # type: ignore[misc]

    # -- flush / compaction -----------------------------------------------

    def _maybe_flush(self) -> None:
        if self._memtable.approx_size_bytes() >= self.flush_threshold_bytes:
            self.flush()

    def flush(self) -> Optional[Path]:
        """Writes the current memtable out as a new immutable segment,
        then clears the memtable and truncates the WAL (everything that
        was in it is now durable in the new segment). A no-op if the
        memtable is empty."""
        with self._lock:
            if len(self._memtable) == 0:
                return None
            segment_id = self._next_segment_id
            self._next_segment_id += 1
            path = self.directory / f"segment-{segment_id:08d}.sst"
            items = list(self._memtable.items_sorted())
            SSTableWriter.write(path, items, num_items_hint=len(items))

            self._readers.append(SSTableReader(path))
            self._segment_paths.append(path)
            self._memtable.clear()
            self._wal.clear()
            return path

    def compact(self) -> None:
        """Merges every on-disk segment into a single new segment, keeping
        only the newest value for each key and dropping tombstones for
        good. Bounds the number of segments a read has to touch, which is
        the whole reason real LSM-tree engines compact in the background.
        """
        with self._lock:
            if len(self._readers) <= 1:
                return

            merged: Dict[bytes, Value] = {}
            for reader in self._readers:  # oldest to newest -> newest wins
                for k, v in reader.scan():
                    merged[k] = v

            old_paths = list(self._segment_paths)
            old_readers = list(self._readers)

            segment_id = self._next_segment_id
            self._next_segment_id += 1
            new_path = self.directory / f"segment-{segment_id:08d}.sst"
            live_items = [(k, v) for k, v in sorted(merged.items()) if v is not TOMBSTONE]
            SSTableWriter.write(new_path, live_items, num_items_hint=max(1, len(live_items)))

            self._readers = [SSTableReader(new_path)]
            self._segment_paths = [new_path]

            for reader in old_readers:
                reader.close()
            for p in old_paths:
                os.remove(p)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._wal.close()
            for reader in self._readers:
                reader.close()

    def __enter__(self) -> "LSMEngine":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
