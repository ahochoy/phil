from phil.config import PhilConfig
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import bad_green, review, tester_report, write_green, write_red


def test_three_failed_greens_escalate(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    result = harness.start()
    escalation = result["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["options"] == ["retry", "skip", "abort"]
    assert escalation["summary"] == "CALC-001 failed 3 attempts in the green phase"
    assert "tests still failing" in escalation["problems"][0]


def test_retry_with_hint_reaches_the_implementer(make_harness):
    harness = make_harness(
        {
            "implementer": [write_red, bad_green, bad_green, bad_green, write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    harness.start()
    final = harness.resume({"action": "retry", "hint": "use a minus sign"})
    assert final["status"] == "completed"
    last_packet = [p for role, p in harness.factory.calls if role == "implementer"][-1]["messages"][0]["content"]
    assert "Human hint: use a minus sign" in last_packet
    assert harness.run_record().needs_attention is None


def test_skip_resets_the_worktree_and_marks_the_task(make_harness, calc_repo):
    harness = make_harness(
        {
            "implementer": [write_red, bad_green, bad_green, bad_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    harness.start()
    final = harness.resume({"action": "skip"})
    assert final["status"] == "completed"
    assert load_plan(final).tasks[0].status == "SKIPPED"
    assert not (harness.deps.worktree / "tests" / "test_sub.py").exists()
    assert "subtract" not in (harness.deps.worktree / "calc.py").read_text()
    assert run_git(calc_repo, "log", "--format=%s", "phil/r-0001").splitlines()[0] == "init"
    assert "(SKIPPED)" in (harness.deps.artifacts.run_dir / "summary.md").read_text()


def test_abort_finishes_as_aborted(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    harness.start()
    final = harness.resume({"action": "abort"})
    assert final["status"] == "aborted"
    assert harness.run_record().state == "aborted"


def test_rejected_output_counts_as_an_attempt(make_harness):
    harness = make_harness(
        {"implementer": [{}, {}, write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
    outcomes = [row["outcome"] for row in harness.deps.conn.execute("SELECT outcome FROM telemetry ORDER BY id")]
    assert outcomes[:2] == ["invalid", "invalid"]


def test_attempt_cap_comes_from_config(make_harness):
    config = PhilConfig.model_validate({"run": {"max_attempts_per_phase": 1}})
    harness = make_harness({"implementer": [write_red, bad_green]}, config=config)
    result = harness.start()
    assert result["__interrupt__"][0].value["summary"] == "CALC-001 failed 1 attempts in the green phase"


def test_invalid_action_asks_again_and_a_valid_one_still_works(make_harness):
    harness = make_harness(
        {
            "implementer": [write_red, bad_green, bad_green, bad_green, write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    harness.start()
    again = harness.resume({"action": "approve"})["__interrupt__"][0].value
    assert "unknown escalation action 'approve'" in again["error"]
    assert again["options"] == ["retry", "skip", "abort"]
    again = harness.resume("not a dict")["__interrupt__"][0].value
    assert "unknown escalation action None" in again["error"]
    final = harness.resume({"action": "retry"})
    assert final["status"] == "completed"
