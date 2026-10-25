from featherdb.engine import LSMEngine


def test_compact_merges_segments_and_resolves_overwrites(tmp_path):
    engine = LSMEngine(tmp_path / "db", flush_threshold_bytes=1)  # flush after every write
    engine.put(b"a", b"1")
    engine.put(b"a", b"2")
    engine.put(b"b", b"x")

    segments_before = list((tmp_path / "db").glob("segment-*.sst"))
    assert len(segments_before) >= 2

    engine.compact()

    segments_after = list((tmp_path / "db").glob("segment-*.sst"))
    assert len(segments_after) == 1
    assert engine.get(b"a") == b"2"
    assert engine.get(b"b") == b"x"
    engine.close()


def test_compact_permanently_drops_tombstoned_keys(tmp_path):
    engine = LSMEngine(tmp_path / "db", flush_threshold_bytes=1)
    engine.put(b"a", b"1")
    engine.delete(b"a")

    engine.compact()

    assert engine.get(b"a") is None
    # The key shouldn't just resolve to None -- it should actually be gone
    # from the merged segment, not lingering as a tombstone forever.
    merged_segment = next((tmp_path / "db").glob("segment-*.sst"))
    from featherdb.sstable import SSTableReader

    reader = SSTableReader(merged_segment)
    from featherdb.memtable import MISSING

    assert reader.get(b"a") is MISSING
    reader.close()
    engine.close()


def test_compact_is_a_no_op_with_zero_or_one_segments(tmp_path):
    engine = LSMEngine(tmp_path / "db")
    engine.compact()  # zero segments: should not raise
    engine.put(b"a", b"1")
    engine.flush()
    engine.compact()  # exactly one segment: should not raise or change anything
    assert engine.get(b"a") == b"1"
    engine.close()


def test_scan_after_compaction_matches_scan_before(tmp_path):
    engine = LSMEngine(tmp_path / "db", flush_threshold_bytes=64)
    for i in range(50):
        engine.put(f"k{i:03d}".encode(), f"v{i}".encode())
    for i in range(0, 50, 3):
        engine.delete(f"k{i:03d}".encode())
    engine.flush()

    before = dict(engine.scan())
    engine.compact()
    after = dict(engine.scan())

    assert before == after
    engine.close()
