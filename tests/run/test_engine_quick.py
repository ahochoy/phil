import json

import pytest

from phil.agents.fake import Turn
from phil.contracts import Issue, Task, TaskResult
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
    def _make(scripts: dict, *, chat_id: str | None = None, plan=None):
        plan = plan or calc_plan()
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


def change_nothing(turn: Turn) -> TaskResult:
    return task_result("green")  # the check passes as it stands; no fix commit


def test_a_fix_that_changes_nothing_escalates_instead_of_finishing(quick_harness, calc_repo):
    harness = quick_harness(
        {
            "quick_implementer": [write_red, write_green, change_nothing],
            "reviewer": [review("changes", [Issue(severity="major", note=FINDING)])],
        },
        chat_id=CHAT_ID,
    )
    result = harness.start()
    escalation = result["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["summary"] == "CALC-001's fix after review didn't pass the gate"
    assert escalation["problems"] == ["no changes were made for CALC-001"]
    assert escalation["options"] == ["full", "retry", "abort"]
    assert result.get("status") != "completed"
    assert harness.run_record().state != "completed"
    log = run_git(calc_repo, "log", "--format=%s", f"{harness.base_sha}..phil/{RUN_ID}").splitlines()
    assert log == ["CALC-001: Add subtract"]


def test_a_quick_plan_with_more_than_one_task_is_not_patched(quick_harness):
    # The fix after review reopens "the" task: a quick plan has exactly one (spec §4.1). Should one
    # ever have more, the review's findings stay open instead.
    second = Task(
        id="CALC-002", description="Note subtract", acceptance_criteria=["tests pass"], verify="check",
        check_cmd=TEST_CMD,
    )
    harness = quick_harness(
        {
            "quick_implementer": [write_red, write_green, add_docstring],
            "reviewer": [review("changes", [Issue(severity="major", note=FINDING)])],
        },
        plan=calc_plan(second),
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert [task.verify for task in load_plan(final).tasks] == ["tdd", "check"]
    assert harness.factory.remaining() == {"quick_implementer": 0, "reviewer": 0}
    assert not final.get("patching") and final.get("patched_issues") == []
    assert {"severity": "major", "note": FINDING}.items() <= final["open_issues"][-1].items()


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
    unfixed = {"severity": "major", "note": f"not fixed after review: {FINDING}"}
    assert any(unfixed.items() <= issue.items() for issue in handoff["open_issues"])
    assert harness.run_record().state == "aborted"


def test_abort_while_patching_keeps_the_findings_and_the_task_done(quick_harness):
    harness = failed_fix_harness(quick_harness)
    harness.start()
    final = harness.resume({"action": "abort"})
    assert final["status"] == "aborted"
    assert load_plan(final).tasks[0].status == "DONE"
    note = f"not fixed after review: {FINDING}"
    assert {"severity": "major", "note": note}.items() <= final["open_issues"][-1].items()
    summary = (harness.deps.artifacts.run_dir / "summary.md").read_text()
    assert f"- (major) {note}" in summary
    assert "- [x] CALC-001" in summary


def test_the_fix_commits_on_top_of_the_task(quick_harness, calc_repo):
    harness = quick_harness({
        "quick_implementer": [write_red, write_green, add_docstring],
        "reviewer": [review("changes", [Issue(severity="major", note=FINDING)])],
    })
    final = harness.start()
    assert final["patching"] is False
    log = run_git(calc_repo, "log", "--format=%s", f"{harness.base_sha}..phil/{RUN_ID}").splitlines()
    assert log == ["CALC-001: fix after review", "CALC-001: Add subtract"]


def test_retry_while_patching_keeps_the_limit_at_one(quick_harness):
    harness = quick_harness(
        {
            "quick_implementer": [write_red, write_green, bad_green, bad_green, add_docstring],
            "reviewer": [review("changes", [Issue(severity="major", note=FINDING)])],
        },
        chat_id=CHAT_ID,
    )
    harness.start()
    escalation = harness.resume({"action": "retry"})["__interrupt__"][0].value
    assert escalation["summary"] == "CALC-001's fix after review didn't pass the gate"
    final = harness.resume({"action": "retry"})
    assert final["status"] == "completed"
    assert harness.factory.remaining() == {"quick_implementer": 0, "reviewer": 0}


def edit_own_test(turn: Turn) -> TaskResult:
    test = turn.workdir / "tests" / "test_sub.py"
    test.write_text(test.read_text() + "\n\ndef test_subtract_negative():\n    assert subtract(1, 3) == -2\n")
    return task_result("green", ["tests/test_sub.py"])


def edit_other_test(turn: Turn) -> TaskResult:
    test = turn.workdir / "tests" / "test_calc.py"
    test.write_text(test.read_text() + "\n\ndef test_add_zero():\n    assert add(0, 0) == 0\n")
    return task_result("green", ["tests/test_calc.py"])


def test_the_fix_may_edit_the_tasks_own_tests(quick_harness):
    harness = quick_harness({
        "quick_implementer": [write_red, write_green, edit_own_test],
        "reviewer": [review("changes", [Issue(severity="major", note="test a negative result")])],
    })
    final = harness.start()
    assert final["status"] == "completed"
    assert final["patch_editable_tests"] == ["tests/test_sub.py"]


def test_the_fix_may_not_edit_other_tests(quick_harness):
    harness = quick_harness({
        "quick_implementer": [write_red, write_green, edit_other_test],
        "reviewer": [review("changes", [Issue(severity="major", note="test add with zeros")])],
    })
    escalation = harness.start()["__interrupt__"][0].value
    assert "check task modified test files: tests/test_calc.py" in escalation["problems"]


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
