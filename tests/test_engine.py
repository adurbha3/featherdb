from featherdb.engine import LSMEngine


def test_put_get_delete_round_trip(tmp_path):
    engine = LSMEngine(tmp_path / "db")
    engine.put(b"a", b"1")
    assert engine.get(b"a") == b"1"

    engine.delete(b"a")
    assert engine.get(b"a") is None
    engine.close()


def test_get_on_unknown_key_returns_none(tmp_path):
    engine = LSMEngine(tmp_path / "db")
    assert engine.get(b"nope") is None
    engine.close()


def test_overwriting_a_key_returns_the_newest_value(tmp_path):
    engine = LSMEngine(tmp_path / "db")
    engine.put(b"a", b"first")
    engine.put(b"a", b"second")
    assert engine.get(b"a") == b"second"
    engine.close()


def test_flush_moves_data_to_a_segment_and_memtable_still_reads_correctly(tmp_path):
    engine = LSMEngine(tmp_path / "db")
    for i in range(10):
        engine.put(f"k{i}".encode(), f"v{i}".encode())

    assert engine.flush() is not None
    assert len(list(tmp_path.glob("db/segment-*.sst"))) == 1

    for i in range(10):
        assert engine.get(f"k{i}".encode()) == f"v{i}".encode()
    engine.close()


def test_flush_is_a_no_op_on_an_empty_memtable(tmp_path):
    engine = LSMEngine(tmp_path / "db")
    assert engine.flush() is None
    engine.close()


def test_automatic_flush_triggers_past_the_byte_threshold(tmp_path):
    engine = LSMEngine(tmp_path / "db", flush_threshold_bytes=256)
    for i in range(100):
        engine.put(f"key-{i:04d}".encode(), (b"x" * 20))
    segments = list((tmp_path / "db").glob("segment-*.sst"))
    assert len(segments) >= 1, "expected at least one automatic flush to have happened"
    engine.close()


def test_a_value_overwritten_across_a_flush_still_resolves_to_the_newest(tmp_path):
    engine = LSMEngine(tmp_path / "db", flush_threshold_bytes=1)  # flush after every write
    engine.put(b"a", b"old")
    engine.put(b"a", b"new")  # forces a flush of "old" into a segment, then "new" lands fresh
    assert engine.get(b"a") == b"new"
    engine.close()


def test_delete_after_flush_shadows_the_flushed_value(tmp_path):
    engine = LSMEngine(tmp_path / "db", flush_threshold_bytes=1)
    engine.put(b"a", b"1")  # flushed to a segment
    engine.delete(b"a")  # tombstone in the (now fresh) memtable
    assert engine.get(b"a") is None
    engine.close()


def test_scan_merges_memtable_and_segments_newest_wins_and_skips_tombstones(tmp_path):
    engine = LSMEngine(tmp_path / "db", flush_threshold_bytes=1)
    engine.put(b"a", b"1")  # -> segment 1
    engine.put(b"b", b"2")  # -> segment 2
    engine.put(b"a", b"1-updated")  # -> segment 3, shadows segment 1's "a"
    engine.delete(b"b")  # tombstone in memtable, shadows segment 2's "b"
    engine.put(b"c", b"3")  # stays in the memtable (tiny write after a delete doesn't force a flush here)

    results = dict(engine.scan())
    assert results == {b"a": b"1-updated", b"c": b"3"}
    engine.close()


def test_scan_range_bounds_are_half_open(tmp_path):
    engine = LSMEngine(tmp_path / "db")
    for i in range(10):
        engine.put(f"k{i:02d}".encode(), b"x")
    keys = [k for k, _ in engine.scan(b"k02", b"k05")]
    assert keys == [b"k02", b"k03", b"k04"]
    engine.close()
