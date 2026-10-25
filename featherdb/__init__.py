"""FeatherDB: a small, zero-dependency LSM-tree storage engine, with a
MongoDB-shaped document API (JSON documents + hash secondary indexes)
built on top of it.
"""

from .bloom import BloomFilter
from .document import Collection
from .engine import LSMEngine
from .memtable import MemTable, TOMBSTONE
from .sstable import SSTableReader, SSTableWriter
from .wal import Op, WriteAheadLog

__all__ = [
    "BloomFilter",
    "Collection",
    "LSMEngine",
    "MemTable",
    "TOMBSTONE",
    "SSTableReader",
    "SSTableWriter",
    "Op",
    "WriteAheadLog",
]
