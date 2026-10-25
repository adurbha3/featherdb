# FeatherDB

A small, zero-dependency LSM-tree storage engine written from scratch in
Python, with a MongoDB-shaped document API (JSON documents + hash secondary
indexes) built on top of it.

FeatherDB is a from-scratch reimplementation of the ideas behind
LevelDB/RocksDB/WiredTiger-style storage engines -- a write-ahead log, an
in-memory memtable, immutable sorted on-disk segments, bloom filters, sparse
indexes, and background-style compaction -- at a scale meant for reading,
testing, and understanding, not for production traffic.

## Why

Document databases like MongoDB are, underneath their query language, a
storage engine (historically MMAPv1, now WiredTiger) plus a document layer
on top of it. This project builds that stack from the bottom up: a real
LSM-tree key/value engine first, then a thin document API over it, so every
layer -- durability, compaction, indexing -- is something I implemented and
tested myself rather than something a library gave me for free.

## Architecture

```
┌─────────────────────────────────────────────┐
│               Collection (document.py)       │
│   JSON documents keyed by _id, hash indexes   │
└───────────────────────┬───────────────────────┘
                         │ get / put / delete / scan
┌───────────────────────▼───────────────────────┐
│               LSMEngine (engine.py)            │
│  ┌───────────┐   ┌──────────────┐   ┌────────┐ │
│  │    WAL    │   │   MemTable   │   │Segments│ │
│  │ (wal.py)  │◄──┤ (memtable.py)│──►│(.sst)  │ │
│  └───────────┘   └──────────────┘   └────────┘ │
└─────────────────────────────────────────────────┘
```

**Write path:** every `put`/`delete` is appended to a write-ahead log and
fsync'd before it touches the in-memory memtable, so an acknowledged write
survives a crash even though it may not have reached disk as a sorted
segment yet. Once the memtable crosses a configurable byte threshold, it is
flushed to a new immutable `segment-NNNNNNNN.sst` file and the WAL is
truncated.

**Read path:** a point lookup checks the memtable first, then each on-disk
segment from newest to oldest (the first segment holding any information
about the key -- a real value or a tombstone -- wins). Each segment carries
its own bloom filter, so a segment that never held the key can usually be
ruled out with zero disk reads, and its own sparse index (one entry every 16
records), so even a "yes, keep looking" still only costs a binary search
plus a short linear scan rather than a full file read.

**Compaction:** `compact()` merges every on-disk segment into one, keeping
only the newest value per key and dropping tombstones for good. Real
engines run this continuously in the background on size-tiered or leveled
schedules; FeatherDB exposes it as a method you call, which is enough to
demonstrate (and test) why it matters -- without it, the number of segments
a read has to check only grows.

**Document layer:** `Collection` stores each JSON document under
`doc:{_id}`, assigning a UUID when the caller doesn't supply one. A prefix
scan (`doc:` to the next byte after `:`) backs `find_all()`. A secondary
index on a field is built by writing one small key per `(field, value,
doc_id)` tuple -- `idx:{field}:{json(value)}:{doc_id}` -- rather than one
growing JSON list per value; this keeps every index write O(1) instead of
rewriting an ever-larger blob on each insert (an earlier version of this
project did exactly that, and it made building an index over n documents
cost O(n^2) -- see "Design notes" below), and a lookup becomes a short range
scan over the shared key prefix.

## Example

```python
from featherdb.document import Collection
from featherdb.engine import LSMEngine

with LSMEngine("./mydata") as engine:
    people = Collection(engine)
    people.create_index("team")

    alice_id = people.insert_one({"name": "Alice", "team": "infra"})
    people.insert_one({"name": "Bob", "team": "infra"})
    people.insert_one({"name": "Carol", "team": "kernel"})

    people.update_one(alice_id, {"title": "Staff Engineer"})

    for doc in people.find_by_field("team", "infra"):
        print(doc)
    # {'name': 'Alice', 'team': 'infra', '_id': '...', 'title': 'Staff Engineer'}
    # {'name': 'Bob', 'team': 'infra', '_id': '...'}
```

A command-line front end is also included:

```
python -m featherdb.cli --db ./mydata insert '{"name": "Ada", "lang": "C++"}'
python -m featherdb.cli --db ./mydata index lang
python -m featherdb.cli --db ./mydata find-by lang C++
python -m featherdb.cli --db ./mydata compact
```

## Testing

56 tests across bloom filters, the WAL, the memtable, SSTable segments
(including a 500-record sparse-index stress test), the engine (flush,
compaction, crash recovery), the document/collection API, secondary
indexes, and full crash-and-reopen recovery scenarios for both the raw
engine and the document layer:

```
PYTHONPATH=. pytest tests/ -v
```

Crash recovery is tested by writing through the engine and then simply
*not* calling `close()` or `flush()` -- the next test re-opens the same
directory in a fresh `LSMEngine` and asserts every acknowledged write
(including ones that were never flushed to a segment) is still there,
exercising the exact WAL-replay path a real crash would trigger.

## Benchmarks

`scripts/benchmark.py` measures three things with real timings rather than
estimates, run on this machine:

```
PYTHONPATH=. python3 scripts/benchmark.py
```

```
Write throughput: 20,000 puts (100-byte values) in 4.27s -> 4,684 puts/sec
Bloom filter savings: 5,000 lookups of missing keys across 32 segments:
  0.301s with bloom filters, 0.427s with them disabled -> 1.4x faster
Secondary index speedup: find_by_field over 20,000 docs (92 matches):
  72.96ms full scan vs. 9.50ms indexed -> 7.7x faster
```

The bloom-filter benchmark is deliberately adversarial to itself: the
"missing" keys it looks up are interleaved between real keys throughout the
keyspace (not conveniently sorted before/after everything), so a linear
scan without a bloom filter actually has to walk partway into a block
before it can rule a key out. The index benchmark looks up a value shared
by a small fraction of a 20,000-document collection, which is exactly the
selective-equality-lookup case a secondary index is for.

## Design notes / known limitations

This is a from-scratch learning/portfolio project, not a production
database, and it makes that trade-off visible rather than hiding it:

- The memtable is a plain Python `dict`, sorted on demand for scans and
  flushes. A production engine uses a skip list or a balanced tree so
  scans and flushes don't re-sort from scratch every time.
- `compact()` is a method you call, not a background thread with a
  size-tiered or leveled compaction policy. There's no limit on how many
  segments can pile up between calls.
- The WAL is not segmented or compacted independently of the memtable it
  backs; it is truncated wholesale on every flush.
- Secondary indexes are single-field, equality-only (no range queries, no
  compound indexes). The per-`(field, value, doc_id)` key design described
  above was a deliberate fix during development: an earlier version stored
  one JSON-encoded list of ids per index value, and updating it on every
  insert meant the list was fully re-serialized each time -- O(n) per
  write, O(n^2) to build an index over n documents. That version passed
  every correctness test and still *worked*, but a benchmark run exposed
  it as pathologically slow (and even made the resulting index slower to
  query than a full scan, because building it fragmented the data across
  far more on-disk segments than necessary) -- a good example of why
  tests alone don't catch performance bugs.
- No concurrent-process access: a single `threading.RLock` makes the
  engine safe to share across threads in one process, but two processes
  pointed at the same directory will corrupt each other's writes.

## Project layout

```
featherdb/
├── featherdb/
│   ├── bloom.py       # Bloom filter (Kirsch-Mitzenmacher double hashing)
│   ├── wal.py         # Write-ahead log
│   ├── memtable.py    # In-memory sorted-on-demand write buffer
│   ├── sstable.py      # Immutable on-disk segment format + reader/writer
│   ├── engine.py       # Ties WAL + memtable + segments into an LSM-tree
│   ├── document.py     # MongoDB-shaped document API + secondary indexes
│   └── cli.py          # Command-line front end
├── tests/               # 56 tests, pytest
├── scripts/
│   └── benchmark.py     # Write throughput, bloom filter, and index benchmarks
└── .github/workflows/tests.yml
```
