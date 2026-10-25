"""Measures three things that matter for an LSM-tree store and prints
numbers a README can quote honestly, instead of made-up figures:

  1. Write throughput (puts/sec) through the full WAL + memtable path.
  2. How much a bloom filter saves on point lookups for missing keys, by
     comparing against a monkeypatched reader whose bloom filter always
     says "maybe present" (i.e. what every lookup would cost without one).
  3. How much faster an indexed find_by_field is than a full collection
     scan, as the collection grows.

Run with: PYTHONPATH=. python3 scripts/benchmark.py
"""
from __future__ import annotations

import random
import shutil
import string
import tempfile
import time
from pathlib import Path

from featherdb.document import Collection
from featherdb.engine import LSMEngine
from featherdb.sstable import SSTableReader


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="featherdb-bench-"))


def benchmark_write_throughput(num_writes: int = 20_000) -> None:
    db_dir = _tmp_dir()
    try:
        engine = LSMEngine(db_dir)
        keys = [f"key-{i:08d}".encode() for i in range(num_writes)]
        value = (b"x" * 100)

        start = time.perf_counter()
        for key in keys:
            engine.put(key, value)
        elapsed = time.perf_counter() - start
        engine.close()

        print(f"Write throughput: {num_writes:,} puts (100-byte values) in {elapsed:.3f}s "
              f"-> {num_writes / elapsed:,.0f} puts/sec")
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)


def benchmark_bloom_filter_savings(num_keys: int = 20_000, num_lookups: int = 5_000) -> None:
    db_dir = _tmp_dir()
    try:
        # Small flush threshold so we get several on-disk segments, which is
        # when bloom filters actually start to matter (more segments to
        # potentially have to check per miss).
        engine = LSMEngine(db_dir, flush_threshold_bytes=8 * 1024)
        # Only the EVEN-numbered keys are ever written. The odd ones used
        # for lookups below sort in between real keys throughout the
        # keyspace, so a linear scan without a bloom filter has to walk
        # partway into a block before it can rule one out -- unlike a key
        # that simply sorts before/after everything, which a scan rejects
        # in one comparison regardless of whether a bloom filter exists.
        for i in range(num_keys):
            engine.put(f"key-{2 * i:08d}".encode(), b"v")
        engine.flush()
        engine.close()

        segment_paths = sorted(db_dir.glob("segment-*.sst"))
        missing_keys = [f"key-{2 * i + 1:08d}".encode() for i in range(num_lookups)]

        # With bloom filters doing their job.
        readers = [SSTableReader(p) for p in segment_paths]
        start = time.perf_counter()
        for key in missing_keys:
            for reader in readers:
                reader.get(key)
        with_bloom = time.perf_counter() - start
        for reader in readers:
            reader.close()

        # Without them: patch might_contain to always say "maybe", forcing
        # every lookup to fall through to the on-disk scan it would have
        # needed without the filter.
        readers = [SSTableReader(p) for p in segment_paths]
        for reader in readers:
            reader.bloom.might_contain = lambda _k: True
        start = time.perf_counter()
        for key in missing_keys:
            for reader in readers:
                reader.get(key)
        without_bloom = time.perf_counter() - start
        for reader in readers:
            reader.close()

        speedup = without_bloom / with_bloom if with_bloom > 0 else float("inf")
        print(f"Bloom filter savings: {num_lookups:,} lookups of missing keys across "
              f"{len(segment_paths)} segment(s): {with_bloom:.4f}s with bloom filters, "
              f"{without_bloom:.4f}s with them disabled -> {speedup:.1f}x faster")
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)


def benchmark_secondary_index(num_docs: int = 20_000, num_values: int = 200) -> None:
    db_dir = _tmp_dir()
    try:
        engine = LSMEngine(db_dir, flush_threshold_bytes=64 * 1024)
        collection = Collection(engine)
        rng = random.Random(42)
        target_value = "team-0"
        for i in range(num_docs):
            team = f"team-{rng.randrange(num_values)}"
            collection.insert_one({
                "name": "".join(rng.choices(string.ascii_lowercase, k=8)),
                "team": team,
            })

        start = time.perf_counter()
        scan_results = collection.find_by_field("team", target_value)
        scan_elapsed = time.perf_counter() - start

        collection.create_index("team")

        start = time.perf_counter()
        indexed_results = collection.find_by_field("team", target_value)
        indexed_elapsed = time.perf_counter() - start

        assert {d["_id"] for d in scan_results} == {d["_id"] for d in indexed_results}

        speedup = scan_elapsed / indexed_elapsed if indexed_elapsed > 0 else float("inf")
        print(f"Secondary index speedup: find_by_field over {num_docs:,} docs "
              f"({len(scan_results)} matches): {scan_elapsed * 1000:.2f}ms full scan vs. "
              f"{indexed_elapsed * 1000:.2f}ms indexed -> {speedup:.1f}x faster")
        engine.close()
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)


if __name__ == "__main__":
    benchmark_write_throughput()
    benchmark_bloom_filter_savings()
    benchmark_secondary_index()
