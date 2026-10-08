import json

from phil.store.activity import MAX_DETAIL_CHARS, ActivityLog


def test_start_and_end_append_records_with_one_seq(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.start(task="T1", role="implementer", tool="run_shell", summary="run pytest -q")
    log.end(seq, task="T1", role="implementer", tool="run_shell", summary="run pytest -q",
            result="→ 7 passed", ok=True, detail="7 passed in 0.1s", duration_ms=1200)
    records, offset = log.read()
    assert [r["phase"] for r in records] == ["start", "end"]
    assert records[0]["seq"] == records[1]["seq"] == seq == 1
    assert records[1]["result"] == "→ 7 passed" and records[1]["detail"] == "1.txt"
    assert log.detail_path(1).read_text(encoding="utf-8") == "7 passed in 0.1s"
    assert offset == log.end_offset()


def test_end_without_detail_writes_no_file(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.record(task="T1", role="implementer", tool="read_file", summary="read a.py",
                     result="", ok=True, detail=None, duration_ms=3)
    assert log.find(seq)["detail"] is None
    assert not log.detail_path(seq).exists()


def test_seq_is_seeded_from_an_existing_log(tmp_path):
    first = ActivityLog(tmp_path)
    first.record(task=None, role="engine", tool="gate", summary="gate pytest", result="", ok=True, detail=None, duration_ms=1)
    first.record(task=None, role="engine", tool="gate", summary="gate pytest", result="", ok=True, detail=None, duration_ms=1)
    assert ActivityLog(tmp_path).start(task=None, role="r", tool="t", summary="s") == 3


def test_a_half_written_line_is_not_read_until_complete(tmp_path):
    log = ActivityLog(tmp_path)
    log.record(task="T1", role="r", tool="t", summary="s", result="", ok=True, detail=None, duration_ms=1)
    with log.path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"seq": 9, "phase": "st')
    records, offset = log.read()
    assert [r["seq"] for r in records] == [1, 1]
    with log.path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('art", "summary": "x"}\n')
    more, _ = log.read(offset)
    assert more == [{"seq": 9, "phase": "start", "summary": "x"}]


def test_a_malformed_complete_line_is_skipped(tmp_path):
    log = ActivityLog(tmp_path)
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text("not json\n" + json.dumps({"seq": 1, "phase": "start"}) + "\n", encoding="utf-8")
    records, _ = log.read()
    assert records == [{"seq": 1, "phase": "start"}]


def test_pending_is_the_newest_start_without_an_end(tmp_path):
    log = ActivityLog(tmp_path)
    done = log.start(task="T1", role="r", tool="read_file", summary="read a")
    log.end(done, task="T1", role="r", tool="read_file", summary="read a", result="", ok=True, detail=None, duration_ms=1)
    log.start(task="T1", role="r", tool="run_shell", summary="run pytest")
    assert log.pending()["summary"] == "run pytest"


def test_detail_is_capped(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.record(task=None, role="r", tool="t", summary="s", result="", ok=True,
                     detail="x" * (MAX_DETAIL_CHARS + 10), duration_ms=1)
    text = log.detail_path(seq).read_text(encoding="utf-8")
    assert text.startswith("x" * MAX_DETAIL_CHARS) and text.endswith("(10 more characters not shown)")


def test_a_write_failure_disables_the_log_and_never_raises(tmp_path, monkeypatch, caplog):
    log = ActivityLog(tmp_path / "missing")
    monkeypatch.setattr(type(log.path), "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert log.start(task=None, role="r", tool="t", summary="s") is None
    assert log.start(task=None, role="r", tool="t", summary="s") is None
    assert sum("activity log disabled" in r.message for r in caplog.records) == 1


def test_detail_for_returns_the_path_only_when_a_detail_file_exists(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.record(task="T1", role="implementer", tool="run_shell", summary="run pytest -q",
                     result="→ 7 passed", ok=True, detail="7 passed in 0.1s", duration_ms=1200)
    assert log.detail_for(seq) == log.detail_path(seq)

    no_detail = log.record(task="T1", role="implementer", tool="read_file", summary="read a.py",
                           result="", ok=True, detail=None, duration_ms=3)
    assert log.detail_for(no_detail) is None

    assert log.detail_for(seq + 100) is None  # no such record

    log.detail_path(seq).unlink()  # the record names a detail file that's gone
    assert log.detail_for(seq) is None


def test_a_corrupted_seq_does_not_raise(tmp_path):
    log = ActivityLog(tmp_path)
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text(json.dumps({"seq": "x", "phase": "start"}) + "\n", encoding="utf-8", newline="\n")
    assert ActivityLog(tmp_path).start(task=None, role="r", tool="t", summary="s") is None
    assert ActivityLog(tmp_path).record(task=None, role="r", tool="t", summary="s", result="",
                                        ok=True, detail=None, duration_ms=1) is None
    assert ActivityLog(tmp_path).last_seq == 0


def test_extra_fields_are_merged_but_never_override(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.start(task="T", role="r", tool="t", summary="s", extra={"sub_id": 3, "sub": "x", "seq": 99})
    log.end(seq, task="T", role="r", tool="t", summary="s", result="", ok=True, detail=None, duration_ms=1,
            extra={"sub_id": 3, "sub": "x"})
    start, end = log.read()[0]
    assert start["seq"] == seq and start["sub_id"] == 3 and end["sub"] == "x"
