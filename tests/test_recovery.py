from featherdb.document import Collection
from featherdb.engine import LSMEngine


def test_engine_recovers_unflushed_writes_from_the_wal_after_a_crash(tmp_path):
    db_dir = tmp_path / "db"
    engine = LSMEngine(db_dir)
    for i in range(20):
        engine.put(f"k{i:03d}".encode(), f"v{i}".encode())
    engine.delete(b"k005")
    # No flush() and no close(): simulates a process that crashed with data
    # only in the WAL, never reaching a segment file.

    recovered = LSMEngine(db_dir)
    for i in range(20):
        key = f"k{i:03d}".encode()
        if i == 5:
            assert recovered.get(key) is None
        else:
            assert recovered.get(key) == f"v{i}".encode()
    recovered.close()


def test_engine_recovers_a_mix_of_flushed_segments_and_unflushed_wal_writes(tmp_path):
    db_dir = tmp_path / "db"
    engine = LSMEngine(db_dir)
    for i in range(10):
        engine.put(f"a{i:03d}".encode(), f"flushed-{i}".encode())
    engine.flush()
    for i in range(10):
        engine.put(f"b{i:03d}".encode(), f"unflushed-{i}".encode())
    # Crash again: "a" keys are safely in a segment, "b" keys only in the WAL.

    recovered = LSMEngine(db_dir)
    for i in range(10):
        assert recovered.get(f"a{i:03d}".encode()) == f"flushed-{i}".encode()
        assert recovered.get(f"b{i:03d}".encode()) == f"unflushed-{i}".encode()
    recovered.close()


def test_engine_recovery_replays_overwrites_and_deletes_in_order(tmp_path):
    db_dir = tmp_path / "db"
    engine = LSMEngine(db_dir)
    engine.put(b"a", b"1")
    engine.put(b"a", b"2")
    engine.delete(b"a")
    engine.put(b"a", b"3")

    recovered = LSMEngine(db_dir)
    assert recovered.get(b"a") == b"3"
    recovered.close()


def test_a_clean_close_then_reopen_also_preserves_data(tmp_path):
    db_dir = tmp_path / "db"
    engine = LSMEngine(db_dir)
    engine.put(b"a", b"1")
    engine.flush()
    engine.put(b"b", b"2")  # left in the memtable/WAL
    engine.close()

    reopened = LSMEngine(db_dir)
    assert reopened.get(b"a") == b"1"
    assert reopened.get(b"b") == b"2"
    reopened.close()


def test_document_collection_survives_a_crash_and_reopen(tmp_path):
    db_dir = tmp_path / "db"
    engine = LSMEngine(db_dir)
    coll = Collection(engine)
    coll.create_index("team")
    ids = [coll.insert_one({"name": f"person{i}", "team": "infra" if i % 2 == 0 else "kernel"}) for i in range(20)]
    coll.delete_one(ids[3])
    coll.update_one(ids[4], {"team": "kernel"})
    # No close(): simulate a crash with everything still in the WAL/memtable.

    recovered_engine = LSMEngine(db_dir)
    recovered_coll = Collection(recovered_engine)

    all_docs = {doc["_id"]: doc for doc in recovered_coll.find_all()}
    assert ids[3] not in all_docs
    assert len(all_docs) == 19
    assert all_docs[ids[4]]["team"] == "kernel"

    # The index itself is not automatically recreated after a crash (it is
    # only maintained incrementally once create_index has been called in
    # the running process), but find_by_field without an index must still
    # give correct answers via a full scan.
    infra_people = recovered_coll.find_by_field("team", "infra")
    assert all(doc["team"] == "infra" for doc in infra_people)
    assert ids[3] not in [doc["_id"] for doc in infra_people]
    recovered_engine.close()
