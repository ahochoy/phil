from phil.agents.fake import Turn
from phil.contracts import Issue, Plan, Task, TaskResult
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


def test_a_check_task_that_keeps_failing_escalates_without_green_phase_wording(make_harness):
    harness = make_harness({"implementer": [write_page_and_touch_tests] * 3}, plan=check_plan())
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["reason"] == "attempts"
    assert escalation["summary"] == "CALC-001 failed 3 attempts on the check task"
    assert "check task modified test files: tests/test_calc.py" in escalation["problems"]
    assert not any("green" in problem for problem in escalation["problems"])


def write_nothing(turn: Turn) -> TaskResult:
    return task_result("green")  # writes nothing at all


def test_a_check_task_that_changes_nothing_fails_then_escalates(make_harness):
    harness = make_harness({"implementer": [write_nothing] * 3}, plan=check_plan())
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["reason"] == "attempts"
    assert escalation["summary"] == "CALC-001 failed 3 attempts on the check task"
    assert escalation["problems"] == ["no changes were made for CALC-001"]
    assert len(implementer_packets(harness)) == 3
    assert "no changes were made for CALC-001" in implementer_packets(harness)[1]


def test_a_check_that_passes_untouched_still_needs_a_change(make_harness):
    # The live failure: "check still passes" held without any change, so the gate passed.
    plan = check_plan()
    plan = plan.model_copy(update={"tasks": [plan.tasks[0].model_copy(update={"check_cmd": "true"})]})
    harness = make_harness({"implementer": [write_nothing] * 3}, plan=plan)
    escalation = harness.start()["__interrupt__"][0].value

    assert escalation["problems"] == ["no changes were made for CALC-001"]


def test_a_check_task_that_writes_after_changing_nothing_passes(make_harness):
    harness = make_harness(
        {"implementer": [write_nothing, write_page], "tester": [tester_report()], "reviewer": [review()]},
        plan=check_plan(),
    )
    final = harness.start()

    assert final["status"] == "completed"
    assert harness.factory.remaining() == {"implementer": 0, "tester": 0, "reviewer": 0}


def write_titled_page(turn: Turn) -> TaskResult:
    (turn.workdir / "index.html").write_text("<title>easter</title>\n<meta name='easter-egg' content='hello world'>\n")
    return task_result("green", ["index.html"])


def test_a_review_finding_in_an_all_check_run_becomes_a_check_task(make_harness):
    issue = Issue(severity="major", note="add a page title")
    harness = make_harness(
        {
            "implementer": [write_page, write_titled_page],
            "tester": [tester_report()],
            "reviewer": [review("changes", [issue]), review()],
        },
        plan=check_plan(),
    )
    final = harness.start()

    assert final["status"] == "completed"
    fix = load_plan(final).tasks[1]
    assert (fix.id, fix.verify, fix.check_cmd, fix.status) == ("CALC-002", "check", CHECK_CMD, "DONE")
    packets = implementer_packets(harness)
    assert len(packets) == 2
    assert '"phase": "green"' in packets[1]
    assert harness.factory.remaining() == {"implementer": 0, "tester": 0, "reviewer": 0}
