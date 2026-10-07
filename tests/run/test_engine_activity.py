import time

import phil.run.engine as engine_module
from phil.agents.fake import Turn, fire_tool
from phil.contracts import Plan, Task
from phil.store.activity import activity_log
from phil.store.events import MILESTONE_KINDS
from phil.store.paths import ProjectPaths
from phil.ui.plan_view import _clip
from tests.run.conftest import RUN_ID, TEST_CMD, bad_green, review, tester_report, write_green, write_red


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


def test_attempt_failed_keeps_only_the_first_line_of_a_multi_line_problem(make_harness, monkeypatch):
    """A gate problem of two lines: the attempt_failed milestone's `problem` is its first line."""
    real = engine_module.verify_green
    calls = []

    def two_line_problem_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return ["2 tests failed\n  FAILED tests/test_sub.py::test_subtract"]
        return real(*args, **kwargs)

    monkeypatch.setattr(engine_module, "verify_green", two_line_problem_once)
    harness, _, _ = _activity_harness(
        make_harness,
        {"implementer": [write_red, write_green, write_green], "tester": [tester_report()], "reviewer": [review()]},
    )
    assert harness.start()["status"] == "completed"
    (failed,) = [m for m in _milestones(harness) if m["kind"] == "attempt_failed"]
    assert failed["problem"] == "2 tests failed"


def test_a_long_task_title_is_clipped_in_task_started(make_harness):
    task = Task(id="CALC-001", description="Add subtract " + "x" * 300,
                acceptance_criteria=["subtract(3, 1) == 2"], files_hint=["calc.py"])
    plan = Plan(keyword="CALC", description="Add arithmetic", tasks=[task], test_cmd=TEST_CMD)
    log = activity_log(ProjectPaths("calc-test"), RUN_ID)
    harness = make_harness({"implementer": [write_red, write_green], "tester": [tester_report()],
                            "reviewer": [review()]}, plan=plan, activity=log)
    assert harness.start()["status"] == "completed"
    (started,) = [m for m in _milestones(harness) if m["kind"] == "task_started"]
    assert started["title"] == _clip(task.description) and started["title"].endswith("…")


def test_a_gate_records_its_start_before_the_tests_run_and_its_real_duration(make_harness, monkeypatch):
    """The gate's start record is written before run_tests runs (so the live row shows it), its end
    comes after, and the end's duration_ms is the real run time (> 0 for a run that takes 0.05s)."""
    real = engine_module.run_tests
    open_while_running = []

    def slow_run_tests(*args, **kwargs):
        open_while_running.append(log.pending())
        time.sleep(0.05)
        return real(*args, **kwargs)

    monkeypatch.setattr(engine_module, "run_tests", slow_run_tests)
    harness, log, test_calls = _activity_harness(
        make_harness,
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
    )
    assert harness.start()["status"] == "completed"

    assert open_while_running and len(open_while_running) == len(test_calls)
    assert all(p is not None and p["tool"] == "gate" and p["phase"] == "start" for p in open_while_running)
    records = log.read()[0]
    gate_ends = [r for r in records if r["tool"] == "gate" and r["phase"] == "end"]
    assert len(gate_ends) == len(test_calls)
    for end in gate_ends:
        positions = [i for i, r in enumerate(records) if r["seq"] == end["seq"]]
        assert [records[i]["phase"] for i in positions] == ["start", "end"]
        assert end["duration_ms"] > 0
