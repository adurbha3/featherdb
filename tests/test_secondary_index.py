from featherdb.document import Collection
from featherdb.engine import LSMEngine


def _collection(tmp_path, **kwargs):
    engine = LSMEngine(tmp_path / "db", **kwargs)
    return engine, Collection(engine)


def test_find_by_field_without_an_index_falls_back_to_a_full_scan(tmp_path):
    engine, coll = _collection(tmp_path)
    coll.insert_one({"name": "Ada", "team": "infra"})
    coll.insert_one({"name": "Grace", "team": "infra"})
    coll.insert_one({"name": "Linus", "team": "kernel"})

    results = coll.find_by_field("team", "infra")
    assert sorted(doc["name"] for doc in results) == ["Ada", "Grace"]
    engine.close()


def test_create_index_backfills_existing_documents(tmp_path):
    engine, coll = _collection(tmp_path)
    coll.insert_one({"name": "Ada", "team": "infra"})
    coll.insert_one({"name": "Grace", "team": "infra"})
    coll.insert_one({"name": "Linus", "team": "kernel"})

    coll.create_index("team")

    results = coll.find_by_field("team", "infra")
    assert sorted(doc["name"] for doc in results) == ["Ada", "Grace"]
    assert [doc["name"] for doc in coll.find_by_field("team", "kernel")] == ["Linus"]
    engine.close()


def test_create_index_is_idempotent(tmp_path):
    engine, coll = _collection(tmp_path)
    coll.insert_one({"name": "Ada", "team": "infra"})
    coll.create_index("team")
    coll.create_index("team")  # should not double up index entries

    results = coll.find_by_field("team", "infra")
    assert [doc["name"] for doc in results] == ["Ada"]
    engine.close()


def test_index_stays_consistent_after_insert_once_created(tmp_path):
    engine, coll = _collection(tmp_path)
    coll.create_index("team")
    coll.insert_one({"name": "Ada", "team": "infra"})
    coll.insert_one({"name": "Grace", "team": "infra"})

    results = coll.find_by_field("team", "infra")
    assert sorted(doc["name"] for doc in results) == ["Ada", "Grace"]
    engine.close()


def test_index_stays_consistent_after_update_moves_a_document_between_values(tmp_path):
    engine, coll = _collection(tmp_path)
    coll.create_index("team")
    doc_id = coll.insert_one({"name": "Ada", "team": "infra"})

    coll.update_one(doc_id, {"team": "kernel"})

    assert coll.find_by_field("team", "infra") == []
    assert [doc["name"] for doc in coll.find_by_field("team", "kernel")] == ["Ada"]
    engine.close()


def test_index_stays_consistent_after_delete(tmp_path):
    engine, coll = _collection(tmp_path)
    coll.create_index("team")
    doc_id = coll.insert_one({"name": "Ada", "team": "infra"})
    coll.insert_one({"name": "Grace", "team": "infra"})

    coll.delete_one(doc_id)

    assert [doc["name"] for doc in coll.find_by_field("team", "infra")] == ["Grace"]
    engine.close()


def test_find_by_field_with_an_index_and_no_matches_returns_empty_list(tmp_path):
    engine, coll = _collection(tmp_path)
    coll.create_index("team")
    coll.insert_one({"name": "Ada", "team": "infra"})

    assert coll.find_by_field("team", "nonexistent-team") == []
    engine.close()


def test_index_survives_a_flush(tmp_path):
    engine, coll = _collection(tmp_path, flush_threshold_bytes=1)
    coll.create_index("team")
    for i in range(10):
        coll.insert_one({"name": f"doc{i}", "team": "infra" if i % 2 == 0 else "kernel"})

    infra_names = sorted(doc["name"] for doc in coll.find_by_field("team", "infra"))
    assert infra_names == [f"doc{i}" for i in range(0, 10, 2)]
    engine.close()
