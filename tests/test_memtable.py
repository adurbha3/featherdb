from featherdb.memtable import MISSING, MemTable, TOMBSTONE


def test_put_then_get_round_trips():
    mt = MemTable()
    mt.put(b"a", b"1")
    assert mt.get(b"a") == b"1"


def test_get_on_unknown_key_is_missing_not_none():
    mt = MemTable()
    assert mt.get(b"nope") is MISSING


def test_delete_leaves_a_tombstone_not_a_removal():
    mt = MemTable()
    mt.put(b"a", b"1")
    mt.delete(b"a")
    assert mt.get(b"a") is TOMBSTONE
    assert len(mt) == 1  # the tombstone itself is still an entry


def test_items_sorted_is_in_key_order():
    mt = MemTable()
    for k in [b"c", b"a", b"b"]:
        mt.put(k, b"x")
    assert [k for k, _ in mt.items_sorted()] == [b"a", b"b", b"c"]


def test_approx_size_tracks_replacements_correctly():
    mt = MemTable()
    mt.put(b"key", b"short")
    size_after_first = mt.approx_size_bytes()
    mt.put(b"key", b"a much longer value")
    size_after_replace = mt.approx_size_bytes()
    assert size_after_replace > size_after_first

    mt.delete(b"key")
    size_after_delete = mt.approx_size_bytes()
    assert size_after_delete == len(b"key")  # tombstone contributes a zero-length value


def test_clear_empties_the_table():
    mt = MemTable()
    mt.put(b"a", b"1")
    mt.clear()
    assert len(mt) == 0
    assert mt.approx_size_bytes() == 0
    assert mt.get(b"a") is MISSING
