import pytest

from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import bad_green, review, tester_report, write_green, write_red


def test_one_task_runs_red_green_and_commits(make_harness, calc_repo):
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()

    assert final["status"] == "completed"
    assert load_plan(final).tasks[0].status == "DONE"
    log = run_git(calc_repo, "log", "--format=%s", "phil/r-0001")
    assert log.splitlines()[0] == "CALC-001: Add subtract"
    assert "def subtract" in (harness.deps.worktree / "calc.py").read_text()
    assert "subtract" not in (calc_repo / "calc.py").read_text()
    record = harness.run_record()
    assert (record.state, record.tasks_done, record.tasks_total) == ("completed", 1, 1)
    summary = (harness.deps.artifacts.run_dir / "summary.md").read_text()
    assert "- [x] CALC-001 Add subtract" in summary
    assert harness.factory.remaining() == {"implementer": 0, "tester": 0, "reviewer": 0}


def test_green_phase_receives_the_red_test_report(make_harness):
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    harness.start()
    green_payload = [payload for role, payload in harness.factory.calls if role == "implementer"][1]
    packet_text = green_payload["messages"][0]["content"]
    assert '"phase": "green"' in packet_text
    assert "tests/test_sub.py" in packet_text


def test_implement_resumes_cleanly_after_a_crash_mid_call(make_harness):
    def crash_after_writing(turn):
        write_green(turn)
        raise RuntimeError("process died")

    harness = make_harness(
        {
            "implementer": [write_red, crash_after_writing, write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    with pytest.raises(RuntimeError, match="process died"):
        harness.start()
    final = harness.graph.invoke(None, harness.thread)
    assert final["status"] == "completed"
    assert (harness.deps.worktree / "calc.py").read_text().count("def subtract") == 1


def test_green_retry_starts_from_the_red_snapshot(make_harness):
    harness = make_harness(
        {"implementer": [write_red, bad_green, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert (harness.deps.worktree / "calc.py").read_text().count("def subtract") == 1
