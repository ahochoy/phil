import json

import pytest

from phil.agents.fake import Turn
from phil.contracts import Issue, TaskResult
from phil.run.state import initial_state, load_plan
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run
from tests.helpers import run_git
from tests.run.conftest import (
    RUN_ID,
    TEST_CMD,
    bad_green,
    calc_plan,
    review,
    task_result,
    tester_report,
    write_green,
    write_red,
)

CHAT_ID = "c-20261001-120000"
FINDING = "subtract needs a docstring"


def no_red(turn: Turn) -> TaskResult:
    return task_result("red")  # writes no failing test, so the red gate fails


def add_docstring(turn: Turn) -> TaskResult:
    calc = turn.workdir / "calc.py"
    calc.write_text(calc.read_text().replace("def subtract(a, b):\n", 'def subtract(a, b):\n    """a - b"""\n'))
    return task_result("green", ["calc.py"])


@pytest.fixture
def quick_harness(make_harness, calc_repo):
    def _make(scripts: dict, *, chat_id: str | None = None):
        plan = calc_plan()
        paths = ProjectPaths("calc-test")
        conn = connect(paths.db_path)
        create_run(
            conn, run_id=RUN_ID, keyword=plan.keyword, base_sha=run_git(calc_repo, "rev-parse", "HEAD").strip(),
            worktree=paths.worktree_dir(RUN_ID), tasks_total=1, chat_id=chat_id, depth="quick",
        )
        harness = make_harness(scripts, plan=plan)
        state = initial_state(RUN_ID, plan, harness.base_sha, TEST_CMD, depth="quick")
        harness.start = lambda: harness.graph.invoke(state, harness.thread)
        return harness

    return _make


def roles(harness) -> list[str]:
    return [role for role, _ in harness.factory.calls]


def packets(harness, role: str) -> list[str]:
    return [payload["messages"][0]["content"] for name, payload in harness.factory.calls if name == role]


def test_quick_run_never_calls_the_tester(quick_harness):
    harness = quick_harness({"quick_implementer": [write_red, write_green], "reviewer": [review()]})
    final = harness.start()
    assert final["status"] == "completed"
    assert "tester" not in roles(harness)


def test_quick_run_uses_the_quick_implementer(quick_harness):
    harness = quick_harness({"quick_implementer": [write_red, write_green], "reviewer": [review()]})
    harness.start()
    assert roles(harness).count("quick_implementer") == 2
    assert "implementer" not in roles(harness)


def test_quick_attempts_cap_at_two_and_offer_full_in_a_chat_run(quick_harness):
    harness = quick_harness({"quick_implementer": [no_red, no_red]}, chat_id=CHAT_ID)
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["options"] == ["full", "retry", "abort"]
    assert escalation["summary"] == "CALC-001 failed 2 attempts in the red phase"
    assert harness.factory.remaining() == {"quick_implementer": 0}


def test_quick_run_without_a_chat_offers_retry_and_abort(quick_harness):
    harness = quick_harness({"quick_implementer": [no_red, no_red]})
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["options"] == ["retry", "abort"]


def test_review_findings_are_patched_in_the_same_task(quick_harness):
    harness = quick_harness({
        "quick_implementer": [write_red, write_green, add_docstring],
        "reviewer": [review("changes", [Issue(severity="major", note=FINDING), Issue(severity="minor", note="nit")])],
    })
    final = harness.start()
    plan = load_plan(final)
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert (task.status, task.verify, task.check_cmd) == ("DONE", "check", TEST_CMD)
    assert f"Review: {FINDING}" in task.acceptance_criteria
    fix_packet = packets(harness, "quick_implementer")[2]
    assert f"Review: {FINDING}" in fix_packet and '"verify": "check"' in fix_packet
    assert roles(harness).count("reviewer") == 1
    assert final["status"] == "completed"
    notes = [issue["note"] for issue in final["open_issues"]]
    assert f"fixed after review (unverified): {FINDING}" in notes
    assert "nit" in notes
    patched = next(i for i in final["open_issues"] if i["note"].startswith("fixed after review"))
    assert patched["severity"] == "minor"


def failed_fix_harness(quick_harness, chat_id=CHAT_ID):
    return quick_harness(
        {
            "quick_implementer": [write_red, write_green, bad_green],
            "reviewer": [review("changes", [Issue(severity="major", note=FINDING)])],
        },
        chat_id=chat_id,
    )


def test_a_failed_fix_after_review_escalates_after_one_attempt(quick_harness):
    harness = failed_fix_harness(quick_harness)
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["summary"] == "CALC-001's fix after review didn't pass the gate"
    assert "full" in escalation["options"]
    assert roles(harness).count("quick_implementer") == 3


def test_full_action_aborts_and_writes_the_handoff(quick_harness):
    harness = failed_fix_harness(quick_harness)
    harness.start()
    final = harness.resume({"action": "full"})
    assert final["status"] == "aborted"
    assert final["moved_to_full"] is True
    handoff = json.loads((harness.deps.artifacts.run_dir / "handoff" / "prior_attempt.json").read_text())
    assert len(handoff["worklogs"]) == 1
    assert handoff["worklogs"][0] == final["worklogs"]["CALC-001"]
    assert FINDING in [issue["note"] for issue in handoff["open_issues"]]
    assert harness.run_record().state == "aborted"


def test_full_runs_unchanged(make_harness):
    from tests.run.test_engine_tester import green_multiply, red_multiply

    harness = make_harness({
        "implementer": [write_red, write_green, red_multiply, green_multiply],
        "tester": [tester_report()],
        "reviewer": [review("changes", [Issue(severity="major", note="add multiply(a, b)")]), review()],
    })
    final = harness.start()
    assert [t.id for t in load_plan(final).tasks] == ["CALC-001", "CALC-002"]
    assert "tester" in roles(harness)
    assert "quick_implementer" not in roles(harness)
    assert final["status"] == "completed"
