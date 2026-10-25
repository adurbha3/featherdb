"""A small MongoDB-shaped document API over an LSMEngine: JSON documents
keyed by `_id`, plus optional hash secondary indexes that turn an equality
lookup on a field from an O(n) full-collection scan into an O(n) index
build once and O(1) lookups after -- the same idea a real document
database's index gives you, scaled down to one process and exact-match
queries on one field at a time.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Iterator, List, Optional

from .engine import LSMEngine

Document = Dict[str, Any]

_DOC_PREFIX = b"doc:"


class Collection:
    def __init__(self, engine: LSMEngine):
        self._engine = engine
        self._indexed_fields: List[str] = []

    # -- CRUD ------------------------------------------------------------

    def insert_one(self, document: Document) -> str:
        doc = dict(document)
        doc_id = str(doc.get("_id") or uuid.uuid4())
        doc["_id"] = doc_id
        self._engine.put(_doc_key(doc_id), json.dumps(doc).encode("utf-8"))
        for field in self._indexed_fields:
            if field in doc:
                self._add_to_index(field, doc[field], doc_id)
        return doc_id

    def find_by_id(self, doc_id: str) -> Optional[Document]:
        raw = self._engine.get(_doc_key(doc_id))
        return json.loads(raw) if raw is not None else None

    def update_one(self, doc_id: str, patch: Document) -> bool:
        existing = self.find_by_id(doc_id)
        if existing is None:
            return False
        self._remove_indexed_fields(existing)
        existing.update(patch)
        existing["_id"] = doc_id
        self._engine.put(_doc_key(doc_id), json.dumps(existing).encode("utf-8"))
        self._add_indexed_fields(existing)
        return True

    def delete_one(self, doc_id: str) -> bool:
        existing = self.find_by_id(doc_id)
        if existing is None:
            return False
        self._remove_indexed_fields(existing)
        self._engine.delete(_doc_key(doc_id))
        return True

    def find_all(self) -> Iterator[Document]:
        for _key, value in self._engine.scan(_DOC_PREFIX, _prefix_upper_bound(_DOC_PREFIX)):
            yield json.loads(value)

    # -- secondary index ---------------------------------------------------

    def create_index(self, field: str) -> None:
        """Builds a hash index on `field` by scanning every existing
        document once, then keeps it up to date incrementally as
        insert_one/update_one/delete_one are called from here on."""
        if field in self._indexed_fields:
            return
        self._indexed_fields.append(field)
        for doc in self.find_all():
            if field in doc:
                self._add_to_index(field, doc[field], doc["_id"])

    def find_by_field(self, field: str, value: Any) -> List[Document]:
        if field in self._indexed_fields:
            docs = [self.find_by_id(doc_id) for doc_id in self._read_index_ids(field, value)]
            return [d for d in docs if d is not None]
        return [doc for doc in self.find_all() if doc.get(field) == value]  # no index: fall back to a full scan

    def _add_indexed_fields(self, doc: Document) -> None:
        for field in self._indexed_fields:
            if field in doc:
                self._add_to_index(field, doc[field], doc["_id"])

    def _remove_indexed_fields(self, doc: Document) -> None:
        for field in self._indexed_fields:
            if field in doc:
                self._remove_from_index(field, doc[field], doc["_id"])

    def _index_prefix(self, field: str, value: Any) -> bytes:
        return f"idx:{field}:{json.dumps(value, sort_keys=True)}:".encode("utf-8")

    def _index_key(self, field: str, value: Any, doc_id: str) -> bytes:
        return self._index_prefix(field, value) + doc_id.encode("utf-8")

    def _read_index_ids(self, field: str, value: Any) -> List[str]:
        # One key per (field, value, doc_id) rather than one growing JSON
        # list per (field, value): adding or removing a document from the
        # index is then a single O(1)-sized write instead of a read-modify-
        # write of an ever-larger blob, which would make create_index()
        # cost O(n^2) total as a collection grows. A range scan over the
        # shared prefix -- cheap thanks to the sparse index each segment
        # already keeps -- recovers the id list when it's actually needed.
        prefix = self._index_prefix(field, value)
        upper = _prefix_upper_bound(prefix)
        return [key[len(prefix):].decode("utf-8") for key, _ in self._engine.scan(prefix, upper)]

    def _add_to_index(self, field: str, value: Any, doc_id: str) -> None:
        self._engine.put(self._index_key(field, value, doc_id), b"1")

    def _remove_from_index(self, field: str, value: Any, doc_id: str) -> None:
        self._engine.delete(self._index_key(field, value, doc_id))


def _doc_key(doc_id: str) -> bytes:
    return _DOC_PREFIX + doc_id.encode("utf-8")


def _prefix_upper_bound(prefix: bytes) -> bytes:
    """The smallest key that is NOT prefixed by `prefix`, used as the
    exclusive upper bound of a prefix scan: [prefix, upper_bound())."""
    if not prefix:
        return b""
    return prefix[:-1] + bytes([prefix[-1] + 1])
