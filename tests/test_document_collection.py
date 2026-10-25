from featherdb.document import Collection
from featherdb.engine import LSMEngine


def _collection(tmp_path, **kwargs):
    engine = LSMEngine(tmp_path / "db", **kwargs)
    return engine, Collection(engine)


def test_insert_generates_an_id_and_find_by_id_round_trips(tmp_path):
    engine, coll = _collection(tmp_path)
    doc_id = coll.insert_one({"name": "Ada", "age": 30})

    found = coll.find_by_id(doc_id)
    assert found["name"] == "Ada"
    assert found["age"] == 30
    assert found["_id"] == doc_id
    engine.close()


def test_insert_respects_a_caller_supplied_id(tmp_path):
    engine, coll = _collection(tmp_path)
    doc_id = coll.insert_one({"_id": "custom-id", "name": "Grace"})
    assert doc_id == "custom-id"
    assert coll.find_by_id("custom-id")["name"] == "Grace"
    engine.close()


def test_find_by_id_on_missing_id_returns_none(tmp_path):
    engine, coll = _collection(tmp_path)
    assert coll.find_by_id("does-not-exist") is None
    engine.close()


def test_update_one_merges_fields_and_keeps_the_id(tmp_path):
    engine, coll = _collection(tmp_path)
    doc_id = coll.insert_one({"name": "Ada", "age": 30})

    assert coll.update_one(doc_id, {"age": 31, "title": "Engineer"}) is True

    updated = coll.find_by_id(doc_id)
    assert updated == {"_id": doc_id, "name": "Ada", "age": 31, "title": "Engineer"}
    engine.close()


def test_update_one_on_missing_id_returns_false_and_does_not_raise(tmp_path):
    engine, coll = _collection(tmp_path)
    assert coll.update_one("nope", {"age": 1}) is False
    engine.close()


def test_delete_one_removes_the_document(tmp_path):
    engine, coll = _collection(tmp_path)
    doc_id = coll.insert_one({"name": "Ada"})
    assert coll.delete_one(doc_id) is True
    assert coll.find_by_id(doc_id) is None
    engine.close()


def test_delete_one_on_missing_id_returns_false(tmp_path):
    engine, coll = _collection(tmp_path)
    assert coll.delete_one("nope") is False
    engine.close()


def test_find_all_returns_every_document_and_nothing_else(tmp_path):
    engine, coll = _collection(tmp_path)
    ids = [coll.insert_one({"name": f"doc{i}"}) for i in range(5)]
    coll.delete_one(ids[2])

    found_names = sorted(doc["name"] for doc in coll.find_all())
    assert found_names == ["doc0", "doc1", "doc3", "doc4"]
    engine.close()


def test_find_all_survives_a_flush(tmp_path):
    engine, coll = _collection(tmp_path, flush_threshold_bytes=1)
    for i in range(10):
        coll.insert_one({"name": f"doc{i}"})

    found_names = sorted(doc["name"] for doc in coll.find_all())
    assert found_names == [f"doc{i}" for i in range(10)]
    engine.close()
