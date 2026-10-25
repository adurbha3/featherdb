"""A classic space-efficient probabilistic set-membership structure: never
a false negative, a tunable false-positive rate. Used by SSTableReader so a
lookup for a key that a segment never held can usually be ruled out
without touching disk at all -- the same trick LevelDB/RocksDB/Cassandra
use to keep LSM-tree reads fast as the number of on-disk segments grows.
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterator, Tuple


def _hash_pair(key: bytes) -> Tuple[int, int]:
    """Derives two independent 64-bit hashes from one SHA-256 digest. Used
    with the Kirsch-Mitzenmacher technique below to synthesize as many
    hash functions as we need from just one real digest computation."""
    digest = hashlib.sha256(key).digest()
    h1 = int.from_bytes(digest[:8], "big")
    h2 = int.from_bytes(digest[8:16], "big")
    return h1, h2


class BloomFilter:
    def __init__(self, expected_items: int, false_positive_rate: float = 0.01):
        expected_items = max(1, expected_items)
        false_positive_rate = min(max(false_positive_rate, 1e-6), 0.5)
        # Standard optimal-size / optimal-hash-count formulas for a bloom
        # filter sized for `expected_items` entries at the target FP rate.
        self.num_bits = max(8, math.ceil(-(expected_items * math.log(false_positive_rate)) / (math.log(2) ** 2)))
        self.num_hashes = max(1, round((self.num_bits / expected_items) * math.log(2)))
        self.bits = bytearray((self.num_bits + 7) // 8)

    def _positions(self, key: bytes) -> Iterator[int]:
        h1, h2 = _hash_pair(key)
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % self.num_bits

    def add(self, key: bytes) -> None:
        for pos in self._positions(key):
            self.bits[pos // 8] |= 1 << (pos % 8)

    def might_contain(self, key: bytes) -> bool:
        """False means `key` is DEFINITELY not in the set. True means it
        probably is (or it's a false positive) -- the caller still has to
        check for real."""
        return all(self.bits[pos // 8] & (1 << (pos % 8)) for pos in self._positions(key))

    def to_bytes(self) -> bytes:
        header = self.num_bits.to_bytes(4, "big") + self.num_hashes.to_bytes(4, "big")
        return header + bytes(self.bits)

    @classmethod
    def from_bytes(cls, data: bytes) -> "BloomFilter":
        num_bits = int.from_bytes(data[0:4], "big")
        num_hashes = int.from_bytes(data[4:8], "big")
        obj = cls.__new__(cls)
        obj.num_bits = num_bits
        obj.num_hashes = num_hashes
        obj.bits = bytearray(data[8:])
        return obj
