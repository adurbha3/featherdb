"""A small command-line front end over FeatherDB -- enough to poke at a
database directory from a terminal without writing a Python script first.

    python -m featherdb.cli --db ./mydata put name Antara
    python -m featherdb.cli --db ./mydata get name
    python -m featherdb.cli --db ./mydata insert '{"name": "Ada", "lang": "C++"}'
    python -m featherdb.cli --db ./mydata index lang
    python -m featherdb.cli --db ./mydata find-by lang C++
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .document import Collection
from .engine import LSMEngine


def _is_jsonish(s: str) -> bool:
    s = s.strip()
    if s in ("true", "false", "null"):
        return True
    if s[:1] in ("{", "[", '"'):
        return True
    try:
        float(s)
        return True
    except ValueError:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="featherdb", description="A tiny LSM-tree document store.")
    parser.add_argument("--db", default="./featherdb_data", help="Data directory (created if missing)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("put", help="Set a raw key/value pair")
    p.add_argument("key")
    p.add_argument("value")

    p = sub.add_parser("get", help="Get a raw value by key")
    p.add_argument("key")

    p = sub.add_parser("delete", help="Delete a raw key")
    p.add_argument("key")

    p = sub.add_parser("scan", help="List raw key/value pairs in a range")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)

    p = sub.add_parser("insert", help="Insert a JSON document")
    p.add_argument("json_doc", help='e.g. \'{"name": "Ada", "lang": "C++"}\'')

    p = sub.add_parser("find", help="Find a document by _id")
    p.add_argument("doc_id")

    p = sub.add_parser("index", help="Build a secondary index on a document field")
    p.add_argument("field")

    p = sub.add_parser("find-by", help="Find documents where field == value")
    p.add_argument("field")
    p.add_argument("value")

    sub.add_parser("compact", help="Merge all on-disk segments into one, dropping deleted keys for good")

    args = parser.parse_args(argv)
    engine = LSMEngine(args.db)
    collection = Collection(engine)

    try:
        if args.command == "put":
            engine.put(args.key.encode(), args.value.encode())
        elif args.command == "get":
            value = engine.get(args.key.encode())
            print(value.decode() if value is not None else "(not found)")
        elif args.command == "delete":
            engine.delete(args.key.encode())
        elif args.command == "scan":
            start = args.start.encode() if args.start else None
            end = args.end.encode() if args.end else None
            for k, v in engine.scan(start, end):
                print(f"{k.decode(errors='replace')} = {v.decode(errors='replace')}")
        elif args.command == "insert":
            doc = json.loads(args.json_doc)
            print(collection.insert_one(doc))
        elif args.command == "find":
            doc = collection.find_by_id(args.doc_id)
            print(json.dumps(doc) if doc is not None else "(not found)")
        elif args.command == "index":
            collection.create_index(args.field)
            print(f"index built on '{args.field}'")
        elif args.command == "find-by":
            value = json.loads(args.value) if _is_jsonish(args.value) else args.value
            for doc in collection.find_by_field(args.field, value):
                print(json.dumps(doc))
        elif args.command == "compact":
            engine.compact()
            print("compacted")
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
