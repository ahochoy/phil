from phil.config import PhilConfig
from tests.helpers import run_git
from tests.run.conftest import bad_green, task_result, write_red


def test_a_baseline_collection_error_does_not_hide_green_failures(make_harness, calc_repo):
    (calc_repo / "tests" / "test_tester.py").write_text(
        "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
    )
    run_git(calc_repo, "add", "-A")
    run_git(calc_repo, "commit", "-m", "tester test for missing multiply")
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["phase"] == "green"
    assert "tests/test_sub.py::test_subtract" in escalation["problems"][0]


def test_red_that_deletes_passing_tests_is_rejected(make_harness):
    def red_deleting_tests(turn):
        (turn.workdir / "tests" / "test_calc.py").unlink()
        return write_red(turn)

    config = PhilConfig.model_validate({"run": {"max_attempts_per_phase": 1}})
    harness = make_harness({"implementer": [red_deleting_tests]}, config=config)
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["phase"] == "red"
    assert escalation["problems"] == ["red phase reduced passing tests from 1 to 0"]


def green_writing_nothing(turn):
    return task_result("green")


def test_a_green_attempt_that_changes_nothing_fails_then_escalates(make_harness):
    # The red phase's tests are still in the worktree; green itself changed nothing on top of them.
    harness = make_harness({"implementer": [write_red, *[green_writing_nothing] * 3]})
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["phase"] == "green"
    assert escalation["summary"] == "CALC-001 failed 3 attempts in the green phase"
    assert escalation["problems"] == ["no changes were made for CALC-001"]


def test_a_red_attempt_that_changes_nothing_fails_the_same_way(make_harness):
    config = PhilConfig.model_validate({"run": {"max_attempts_per_phase": 1}})
    harness = make_harness({"implementer": [lambda turn: task_result("red")]}, config=config)
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["phase"] == "red"
    assert escalation["problems"] == ["no changes were made for CALC-001"]
