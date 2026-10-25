from featherdb.wal import Op, WriteAheadLog


def test_replay_reconstructs_writes_in_order(tmp_path):
    wal = WriteAheadLog(tmp_path / "wal.log")
    wal.append(Op.PUT, b"a", b"1")
    wal.append(Op.PUT, b"b", b"2")
    wal.append(Op.DELETE, b"a", None)
    wal.append(Op.PUT, b"a", b"3")

    records = list(wal.replay())
    assert records == [
        (Op.PUT, b"a", b"1"),
        (Op.PUT, b"b", b"2"),
        (Op.DELETE, b"a", None),
        (Op.PUT, b"a", b"3"),
    ]
    wal.close()


def test_replay_on_a_fresh_empty_log_yields_nothing(tmp_path):
    wal = WriteAheadLog(tmp_path / "wal.log")
    assert list(wal.replay()) == []
    wal.close()


def test_clear_truncates_the_log(tmp_path):
    wal = WriteAheadLog(tmp_path / "wal.log")
    wal.append(Op.PUT, b"a", b"1")
    assert len(list(wal.replay())) == 1

    wal.clear()
    assert list(wal.replay()) == []

    wal.append(Op.PUT, b"b", b"2")
    assert list(wal.replay()) == [(Op.PUT, b"b", b"2")]
    wal.close()


def test_replay_tolerates_a_truncated_trailing_record(tmp_path):
    path = tmp_path / "wal.log"
    wal = WriteAheadLog(path)
    wal.append(Op.PUT, b"a", b"1")
    wal.append(Op.PUT, b"b", b"2")
    wal.close()

    # Simulate a crash mid-write: chop off the last few bytes of the log.
    data = path.read_bytes()
    path.write_bytes(data[:-2])

    wal2 = WriteAheadLog(path)
    records = list(wal2.replay())
    # The first full record must still be recovered; the truncated one is
    # silently dropped rather than crashing recovery.
    assert records[0] == (Op.PUT, b"a", b"1")
    wal2.close()
