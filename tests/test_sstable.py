from featherdb.memtable import MISSING, TOMBSTONE
from featherdb.sstable import SSTableReader, SSTableWriter


def _write_segment(path, items):
    SSTableWriter.write(path, items, num_items_hint=len(items))
    return SSTableReader(path)


def test_get_returns_exact_values_for_every_written_key(tmp_path):
    items = [(f"k{i:03d}".encode(), f"v{i}".encode()) for i in range(50)]
    reader = _write_segment(tmp_path / "seg.sst", items)
    for k, v in items:
        assert reader.get(k) == v
    reader.close()


def test_get_on_key_never_written_is_missing(tmp_path):
    items = [(b"a", b"1"), (b"m", b"2"), (b"z", b"3")]
    reader = _write_segment(tmp_path / "seg.sst", items)
    assert reader.get(b"nonexistent") is MISSING
    assert reader.get(b"aa") is MISSING  # between "a" and "m", never written
    reader.close()


def test_get_reports_tombstones_distinctly_from_missing(tmp_path):
    items = [(b"a", b"1"), (b"b", TOMBSTONE), (b"c", b"3")]
    reader = _write_segment(tmp_path / "seg.sst", items)
    assert reader.get(b"b") is TOMBSTONE
    assert reader.get(b"a") == b"1"
    reader.close()


def test_scan_respects_half_open_range_and_order(tmp_path):
    items = [(f"k{i:03d}".encode(), f"v{i}".encode()) for i in range(20)]
    reader = _write_segment(tmp_path / "seg.sst", items)

    results = list(reader.scan(b"k005", b"k010"))
    assert [k for k, _ in results] == [f"k{i:03d}".encode() for i in range(5, 10)]
    reader.close()


def test_scan_with_no_bounds_returns_everything_in_order(tmp_path):
    items = [(f"k{i:03d}".encode(), b"x") for i in range(30)]
    reader = _write_segment(tmp_path / "seg.sst", items)
    results = list(reader.scan())
    assert [k for k, _ in results] == [k for k, _ in items]
    reader.close()


def test_sparse_index_works_across_many_records_spanning_multiple_blocks(tmp_path):
    # Enough records to exercise several sparse-index blocks (interval is 16).
    items = [(f"k{i:05d}".encode(), f"value-{i}".encode()) for i in range(500)]
    reader = _write_segment(tmp_path / "seg.sst", items)
    assert reader.num_entries == 500
    # Spot-check keys that fall mid-block, not just on index boundaries.
    for i in (0, 1, 15, 16, 17, 250, 498, 499):
        assert reader.get(items[i][0]) == items[i][1]
    reader.close()


def test_bad_magic_is_rejected(tmp_path):
    path = tmp_path / "not_a_segment.sst"
    path.write_bytes(b"this is not a FeatherDB segment file" + b"\x00" * 64)
    try:
        SSTableReader(path)
        assert False, "expected ValueError for a corrupt/foreign file"
    except (ValueError, OSError):
        pass
