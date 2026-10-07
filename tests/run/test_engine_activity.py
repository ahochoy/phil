from phil.agents.fake import Turn, fire_tool
from phil.store.activity import activity_log
from phil.store.events import MILESTONE_KINDS
from phil.store.paths import ProjectPaths
from tests.run.conftest import RUN_ID, bad_green, review, tester_report, write_green, write_red


def _red_with_tools(turn: Turn):
    """The red phase, with one edit and one shell command made visible to the invoke callbacks."""
    fire_tool(turn, "edit_file", {"file_path": "tests/test_sub.py", "old_string": "", "new_string": "x\n"}, "ok")
    fire_tool(turn, "run_shell", {"command": "pytest -q"}, "exit_code: 1\n1 failed in 0.01s\n")
    return write_red(turn)


def _activity_harness(make_harness, scripts: dict):
    log = activity_log(ProjectPaths("calc-test"), RUN_ID)
    harness = make_harness(scripts, activity=log)
    test_calls: list[str] = []
    original = harness.engine._test

    def counting_test(state, name, worktree=None):
        test_calls.append(name)
        return original(state, name, worktree)

    harness.engine._test = counting_test
    return harness, log, test_calls


def _milestones(harness) -> list[dict]:
    return [event for event in harness.deps.events.read()[0] if event["kind"] in MILESTONE_KINDS]


def test_a_run_records_tool_lines_gate_lines_and_milestones_in_order(make_harness):
    """Asserts:
    - activity.jsonl has end records for the scripted edit and run_shell (role implementer, task CALC-001)
      and 'gate' records (role engine, tool gate) for each _test call;
    - events.jsonl milestones, in order: task_started(CALC-001), gate(red), gate(green), task_done(CALC-001);
    - every milestone carries an int seq <= the activity log's last_seq at the end."""
    harness, log, test_calls = _activity_harness(
        make_harness,
        {"implementer": [_red_with_tools, write_green], "tester": [tester_report()], "reviewer": [review()]},
    )
    assert harness.start()["status"] == "completed"

    ends = [record for record in log.read()[0] if record["phase"] == "end"]
    tools = [(r["tool"], r["summary"], r["role"], r["task"]) for r in ends if r["role"] == "implementer"]
    assert tools == [
        ("edit_file", "edit tests/test_sub.py", "implementer", "CALC-001"),
        ("run_shell", "run pytest -q", "implementer", "CALC-001"),
    ]
    gates = [r for r in ends if r["tool"] == "gate"]
    assert test_calls and len(gates) == len(test_calls)
    assert all(r["role"] == "engine" for r in gates)

    milestones = _milestones(harness)
    task_milestones = [m for m in milestones if m["kind"] != "verdict"]
    assert [(m["kind"], m["task"], m.get("name")) for m in task_milestones] == [
        ("task_started", "CALC-001", None),
        ("gate", "CALC-001", "red"),
        ("gate", "CALC-001", "green"),
        ("task_done", "CALC-001", None),
    ]
    assert [(m["role"], m["outcome"]) for m in milestones if m["kind"] == "verdict"] == [
        ("tester", "passed"), ("reviewer", "passed"),
    ]
    last_seq = log.last_seq
    assert last_seq > 0
    assert all(isinstance(m["seq"], int) and m["seq"] <= last_seq for m in milestones)


def test_a_failed_attempt_writes_attempt_failed_with_retrying(make_harness):
    """A green attempt that fails the gate once, then passes: attempt_failed(attempt=1, retrying=True)
    appears before task_done."""
    harness, _, _ = _activity_harness(
        make_harness,
        {"implementer": [write_red, bad_green, write_green], "tester": [tester_report()], "reviewer": [review()]},
    )
    assert harness.start()["status"] == "completed"

    kinds = [m["kind"] for m in _milestones(harness)]
    failed = [m for m in _milestones(harness) if m["kind"] == "attempt_failed"]
    assert len(failed) == 1
    assert (failed[0]["task"], failed[0]["attempt"], failed[0]["retrying"]) == ("CALC-001", 1, True)
    assert kinds.index("attempt_failed") < kinds.index("task_done")
