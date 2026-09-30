from phil.agents.fake import Turn
from phil.contracts import Plan, Task, TaskResult
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import TEST_CMD, review, task_result, tester_report

CHECK_CMD = "grep -q easter index.html"


def check_plan():
    task = Task(
        id="CALC-001",
        description="Add the easter egg to the page",
        acceptance_criteria=["index.html contains easter"],
        files_hint=["index.html"],
        verify="check",
        check_cmd=CHECK_CMD,
    )
    return Plan(keyword="CALC", description="Copy change", tasks=[task], test_cmd=TEST_CMD)


def write_page(turn: Turn) -> TaskResult:
    (turn.workdir / "index.html").write_text("<meta name='easter-egg' content='hello world'>\n")
    return task_result("green", ["index.html"])


def write_wrong_page(turn: Turn) -> TaskResult:
    (turn.workdir / "index.html").write_text("<p>hello</p>\n")
    return task_result("green", ["index.html"])


def write_page_and_touch_tests(turn: Turn) -> TaskResult:
    write_page(turn)
    test_file = turn.workdir / "tests" / "test_calc.py"
    test_file.write_text(test_file.read_text() + "\n\ndef test_extra():\n    assert True\n")
    return task_result("green", ["index.html", "tests/test_calc.py"])


def implementer_packets(harness) -> list[str]:
    return [payload["messages"][0]["content"] for role, payload in harness.factory.calls if role == "implementer"]


def test_check_task_skips_red_and_commits_after_one_implement_call(make_harness, calc_repo):
    harness = make_harness(
        {"implementer": [write_page], "tester": [tester_report()], "reviewer": [review()]}, plan=check_plan()
    )
    final = harness.start()

    assert final["status"] == "completed"
    assert load_plan(final).tasks[0].status == "DONE"
    packets = implementer_packets(harness)
    assert len(packets) == 1
    assert '"phase": "green"' in packets[0]
    assert '"verify": "check"' in packets[0] and CHECK_CMD in packets[0]
    log = run_git(calc_repo, "log", "--format=%s", "phil/r-0001")
    assert log.splitlines()[0] == "CALC-001: Add the easter egg to the page"
    assert final["red_tree"] == ""
    logs = harness.deps.artifacts.run_dir / "logs"
    assert (logs / "check-CALC-001-1.log").exists()
    assert harness.factory.remaining() == {"implementer": 0, "tester": 0, "reviewer": 0}


def test_failing_check_cmd_retries_with_the_problem_in_feedback(make_harness):
    harness = make_harness(
        {"implementer": [write_wrong_page, write_page], "tester": [tester_report()], "reviewer": [review()]},
        plan=check_plan(),
    )
    final = harness.start()

    assert final["status"] == "completed"
    packets = implementer_packets(harness)
    assert len(packets) == 2
    assert f"check command failed (exit 1): {CHECK_CMD}" in packets[1]
    assert "easter" in (harness.deps.worktree / "index.html").read_text()


def test_check_task_that_edits_a_test_file_fails_the_gate(make_harness):
    harness = make_harness(
        {"implementer": [write_page_and_touch_tests, write_page], "tester": [tester_report()], "reviewer": [review()]},
        plan=check_plan(),
    )
    final = harness.start()

    assert final["status"] == "completed"
    packets = implementer_packets(harness)
    assert "modified test files: tests/test_calc.py" in packets[1]
    assert "test_extra" not in (harness.deps.worktree / "tests" / "test_calc.py").read_text()
