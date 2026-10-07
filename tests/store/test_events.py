from phil.store.events import EventLog, run_events
from phil.store.paths import ProjectPaths


def test_append_and_read_with_offsets(tmp_path):
    log = EventLog(tmp_path / "run" / "events.jsonl")
    assert log.read() == ([], 0)
    log.append("node", node="setup")
    events, offset = log.read()
    assert [(e["kind"], e["node"]) for e in events] == [("node", "setup")]
    assert "ts" in events[0]
    log.append("state", state="running")
    more, offset2 = log.read(offset)
    assert [e["kind"] for e in more] == ["state"]
    assert log.read(offset2) == ([], offset2)


def test_read_skips_lines_that_are_not_objects(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'[1, 2]\n"text"\n7\n{"ts": "t", "kind": "node", "node": "setup"}\n')
    events, offset = EventLog(path).read()
    assert [e["kind"] for e in events] == ["node"]
    assert offset == path.stat().st_size


def test_end_offset_is_the_log_size_or_zero(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    assert log.end_offset() == 0
    log.append("node", node="setup")
    assert log.end_offset() == log.path.stat().st_size
    assert log.read(log.end_offset()) == ([], log.end_offset())


def test_partial_lines_are_not_returned(tmp_path):
    path = tmp_path / "events.jsonl"
    # Bytes, not write_text: on Windows text mode would write "\r\n" and shift the offset.
    path.write_bytes(b'{"ts": "t", "kind": "node", "node": "a"}\n{"ts": "t", "kind": "no')
    events, offset = EventLog(path).read()
    assert [e["node"] for e in events] == ["a"]
    assert offset == len('{"ts": "t", "kind": "node", "node": "a"}\n')


def test_malformed_complete_lines_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"ts": "t", "kind": "node", "node": "a"}\nnot json\n{"ts": "t", "kind": "node", "node": "b"}\n')
    events, offset = EventLog(path).read()
    assert [e["node"] for e in events] == ["a", "b"]
    assert offset == path.stat().st_size


def test_latest_by_kind(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("escalation", escalation={"summary": "first"})
    log.append("node", node="x")
    log.append("escalation", escalation={"summary": "second"})
    assert log.latest("escalation")["escalation"]["summary"] == "second"
    assert log.latest("outcome") is None


def test_run_events_path(phil_home):
    paths = ProjectPaths("demo-12345678")
    assert run_events(paths, "r-0001").path == paths.run_dir("r-0001") / "events.jsonl"


def test_append_writes_lf_only(tmp_path):
    """Appended lines end with `\\n`, never `\\r\\n`, whatever OS writes them."""
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append("node", node="setup")
    log.append("state", state="running")

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 2
