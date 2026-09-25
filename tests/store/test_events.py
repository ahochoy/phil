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


def test_partial_lines_are_not_returned(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"ts": "t", "kind": "node", "node": "a"}\n{"ts": "t", "kind": "no')
    events, offset = EventLog(path).read()
    assert [e["node"] for e in events] == ["a"]
    assert offset == len('{"ts": "t", "kind": "node", "node": "a"}\n')


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
