"""An append-only durability log: every write is fsync'd here *before* it
is applied to the in-memory memtable, so a crash between those two steps
never loses an acknowledged write -- replay() reconstructs exactly what the
memtable held by re-applying the log in order.
"""
from __future__ import annotations

import os
import struct
from enum import IntEnum
from pathlib import Path
from typing import Iterator, Optional, Tuple, Union


class Op(IntEnum):
    PUT = 1
    DELETE = 2


_HEADER = struct.Struct(">BI")  # op, key_len
_VALUE_LEN = struct.Struct(">I")


class WriteAheadLog:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._file = open(self.path, "ab")

    def append(self, op: Op, key: bytes, value: Optional[bytes]) -> None:
        self._file.write(_HEADER.pack(int(op), len(key)))
        self._file.write(key)
        if op is Op.PUT:
            assert value is not None
            self._file.write(_VALUE_LEN.pack(len(value)))
            self._file.write(value)
        self._file.flush()
        os.fsync(self._file.fileno())

    def replay(self) -> Iterator[Tuple["Op", bytes, Optional[bytes]]]:
        """Reads every record currently on disk, in order. Opens its own
        read handle, so it's safe to call even while this same process
        holds the log open for appending."""
        if not self.path.exists():
            return
        with open(self.path, "rb") as f:
            while True:
                header = f.read(_HEADER.size)
                if len(header) < _HEADER.size:
                    break  # a partial trailing record from a crash mid-write is just dropped
                op_val, key_len = _HEADER.unpack(header)
                key = f.read(key_len)
                if len(key) < key_len:
                    break
                if op_val == Op.PUT:
                    vlen_bytes = f.read(_VALUE_LEN.size)
                    if len(vlen_bytes) < _VALUE_LEN.size:
                        break
                    (vlen,) = _VALUE_LEN.unpack(vlen_bytes)
                    value = f.read(vlen)
                    if len(value) < vlen:
                        break
                    yield Op.PUT, key, value
                else:
                    yield Op.DELETE, key, None

    def clear(self) -> None:
        """Called right after a successful memtable flush: everything that
        was in this log is now durable in an SSTable segment, so the log
        can be truncated instead of growing forever."""
        self._file.close()
        self._file = open(self.path, "wb")
        self._file.close()
        self._file = open(self.path, "ab")

    def close(self) -> None:
        self._file.close()
