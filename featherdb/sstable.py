"""Immutable, sorted, on-disk segment files -- the "SSTable" half of the
LSM-tree. A segment is written once from already-sorted data and never
mutated again; that immutability is what makes reads, writes, and crash
recovery simple to reason about. Each segment carries its own sparse index
(so a point lookup only needs one binary search plus a short linear scan,
not a full file read) and its own bloom filter (so a lookup for a key this
segment never held can usually be ruled out with zero disk reads).
"""
from __future__ import annotations

import bisect
import struct
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, List, Optional, Tuple, Union

from .bloom import BloomFilter
from .memtable import MISSING, TOMBSTONE, Value

_RECORD_HEADER = struct.Struct(">IB")  # key_len, is_tombstone
_VALUE_LEN = struct.Struct(">I")
_INDEX_ENTRY_HEADER = struct.Struct(">IQ")  # key_len, offset
_FOOTER = struct.Struct(">QQQQI4s")  # index_offset, index_len, bloom_offset, bloom_len, num_entries, magic
MAGIC = b"FDB1"

# Every Nth record (in sorted order) gets a sparse-index entry. Smaller =
# faster point lookups, larger = smaller index / less memory per open
# segment. 16 is a reasonable default for a toy engine at this scale.
SPARSE_INDEX_INTERVAL = 16


class SSTableWriter:
    @staticmethod
    def write(path: Union[str, Path], sorted_items: Iterable[Tuple[bytes, Value]], num_items_hint: int) -> None:
        path = Path(path)
        bloom = BloomFilter(expected_items=max(1, num_items_hint))
        index: List[Tuple[bytes, int]] = []

        with open(path, "wb") as f:
            count = 0
            for key, value in sorted_items:
                offset = f.tell()
                bloom.add(key)
                if count % SPARSE_INDEX_INTERVAL == 0:
                    index.append((key, offset))

                is_tombstone = value is TOMBSTONE
                f.write(_RECORD_HEADER.pack(len(key), 1 if is_tombstone else 0))
                f.write(key)
                if not is_tombstone:
                    assert isinstance(value, (bytes, bytearray))
                    f.write(_VALUE_LEN.pack(len(value)))
                    f.write(value)
                count += 1

            index_offset = f.tell()
            for key, offset in index:
                f.write(_INDEX_ENTRY_HEADER.pack(len(key), offset))
                f.write(key)
            index_len = f.tell() - index_offset

            bloom_offset = f.tell()
            bloom_bytes = bloom.to_bytes()
            f.write(bloom_bytes)
            bloom_len = len(bloom_bytes)

            f.write(_FOOTER.pack(index_offset, index_len, bloom_offset, bloom_len, count, MAGIC))


class SSTableReader:
    """Opens an on-disk segment written by SSTableWriter: loads its sparse
    index and bloom filter into memory once (both are small -- the bulk of
    the segment, the actual records, stays on disk), then serves
    get()/scan() by seeking into the data section as needed.
    """

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._file: BinaryIO = open(self.path, "rb")

        self._file.seek(-_FOOTER.size, 2)
        footer_bytes = self._file.read(_FOOTER.size)
        index_offset, index_len, bloom_offset, bloom_len, num_entries, magic = _FOOTER.unpack(footer_bytes)
        if magic != MAGIC:
            raise ValueError(f"{self.path}: not a FeatherDB segment file (bad magic)")
        self.num_entries = num_entries
        self._data_end = index_offset

        self._file.seek(bloom_offset)
        self.bloom = BloomFilter.from_bytes(self._file.read(bloom_len))

        self._file.seek(index_offset)
        index_bytes = self._file.read(index_len)
        self._sparse_index: List[Tuple[bytes, int]] = []
        pos = 0
        while pos < len(index_bytes):
            key_len, offset = _INDEX_ENTRY_HEADER.unpack_from(index_bytes, pos)
            pos += _INDEX_ENTRY_HEADER.size
            key = index_bytes[pos : pos + key_len]
            pos += key_len
            self._sparse_index.append((key, offset))

    def close(self) -> None:
        self._file.close()

    def _block_start_for(self, key: bytes) -> int:
        """Binary-searches the sparse index for the largest indexed key
        <= `key`; its offset is where a linear scan toward `key` should
        begin (0 if `key` is smaller than every indexed key)."""
        keys = [k for k, _ in self._sparse_index]
        i = bisect.bisect_right(keys, key) - 1
        if i < 0:
            return 0
        return self._sparse_index[i][1]

    def _read_record_at_current_pos(self):
        """Returns (key, value_or_TOMBSTONE) or None at end of data."""
        header = self._file.read(_RECORD_HEADER.size)
        if len(header) < _RECORD_HEADER.size:
            return None
        key_len, is_tombstone = _RECORD_HEADER.unpack(header)
        key = self._file.read(key_len)
        if is_tombstone:
            return key, TOMBSTONE
        (val_len,) = _VALUE_LEN.unpack(self._file.read(_VALUE_LEN.size))
        value = self._file.read(val_len)
        return key, value

    def get(self, key: bytes):
        """Returns the stored value, TOMBSTONE, or MISSING (see
        memtable.py for what each means to the caller)."""
        if not self.bloom.might_contain(key):
            return MISSING  # bloom filters never false-negative, so this is certain

        self._file.seek(self._block_start_for(key))
        while self._file.tell() < self._data_end:
            record = self._read_record_at_current_pos()
            if record is None:
                break
            record_key, value = record
            if record_key == key:
                return value
            if record_key > key:
                return MISSING  # sorted file: we've passed where `key` would be
        return MISSING

    def scan(self, start: Optional[bytes] = None, end: Optional[bytes] = None) -> Iterator[Tuple[bytes, Value]]:
        """Yields (key, value_or_TOMBSTONE) for start <= key < end, in
        ascending key order."""
        begin_offset = self._block_start_for(start) if start is not None else 0
        self._file.seek(begin_offset)
        while self._file.tell() < self._data_end:
            record = self._read_record_at_current_pos()
            if record is None:
                break
            key, value = record
            if start is not None and key < start:
                continue
            if end is not None and key >= end:
                return
            yield key, value
