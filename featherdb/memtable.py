"""The in-memory, mutable half of the LSM-tree: recent writes land here
first (after the WAL) and are served from here on reads before any on-disk
segment is consulted.
"""
from __future__ import annotations

from typing import Dict, Iterator, Tuple, Union


class _Tombstone:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<tombstone>"


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<missing>"


# Sentinels, not plain None/False: a stored value can legitimately be empty
# bytes (b""), so "deleted" and "no information about this key at all" each
# need their own unmistakable marker.
TOMBSTONE = _Tombstone()
MISSING = _Missing()

Value = Union[bytes, _Tombstone]


class MemTable:
    """Backed by a plain dict for O(1) point reads/writes; range scans sort
    on demand. That's the right tradeoff at the sizes a memtable is flushed
    at here -- a production engine would use a skip list or a sorted
    balanced tree instead so scans don't re-sort from scratch (see the
    README's design notes).
    """

    def __init__(self) -> None:
        self._data: Dict[bytes, Value] = {}
        self._approx_bytes = 0

    def put(self, key: bytes, value: bytes) -> None:
        self._account_for_replacement(key, value)
        self._data[key] = value

    def delete(self, key: bytes) -> None:
        self._account_for_replacement(key, b"")
        self._data[key] = TOMBSTONE

    def _account_for_replacement(self, key: bytes, new_value: bytes) -> None:
        old = self._data.get(key, MISSING)
        if old is not MISSING:
            old_len = 0 if old is TOMBSTONE else len(old)
            self._approx_bytes -= len(key) + old_len
        self._approx_bytes += len(key) + len(new_value)

    def get(self, key: bytes) -> Union[Value, _Missing]:
        """Returns the stored value, TOMBSTONE if the key was deleted here,
        or MISSING if this memtable has no information about the key at all
        (the caller should then fall through to the on-disk segments)."""
        return self._data.get(key, MISSING)

    def __len__(self) -> int:
        return len(self._data)

    def approx_size_bytes(self) -> int:
        return self._approx_bytes

    def items_sorted(self) -> Iterator[Tuple[bytes, Value]]:
        for key in sorted(self._data.keys()):
            yield key, self._data[key]

    def clear(self) -> None:
        self._data = {}
        self._approx_bytes = 0
