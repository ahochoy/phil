from phil.agents.fake import Turn
from phil.contracts import Plan, Task, TaskResult
from phil.run.state import initial_state, load_plan
from tests.helpers import run_git
from tests.run.conftest import TEST_CMD, bad_green, calc_plan, review, task_result, tester_report, write_green, write_red

CHECK_CMD = "grep -q easter index.html"
NEW_CMD = f"{TEST_CMD} --tb=line"  # a different command with the same results


def check_only_plan() -> Plan:
    task = Task(
        id="CALC-001",
        description="Add the easter egg to the page",
        acceptance_criteria=["index.html contains easter"],
        files_hint=["index.html"],
        verify="check",
        check_cmd=CHECK_CMD,
    )
    return Plan(keyword="CALC", description="Copy change", tasks=[task], test_cmd=None)


def write_page(turn: Turn) -> TaskResult:
    (turn.workdir / "index.html").write_text("<meta name='easter-egg' content='hello world'>\n")
    return task_result("green", ["index.html"])


def start(harness, test_cmd: str) -> dict:
    return harness.graph.invoke(initial_state(harness.deps.run_id, harness.plan, harness.base_sha, test_cmd), harness.thread)


def logs(harness) -> list[str]:
    directory = harness.deps.artifacts.run_dir / "logs"
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


def test_an_all_check_run_with_no_test_command_runs_only_the_check(make_harness):
    harness = make_harness(
        {"implementer": [write_page], "tester": [tester_report()], "reviewer": [review()]}, plan=check_only_plan()
    )
    final = start(harness, "")

    assert final["status"] == "completed"
    assert load_plan(final).tasks[0].status == "DONE"
    assert final["baseline_failures"] == [] and final["base_passed"] is None
    names = logs(harness)
    assert names == ["check-CALC-001-1.log"]  # no test runs at all: no baseline, verify, tester or review logs
    assert harness.factory.remaining() == {"implementer": 0, "tester": 0, "reviewer": 0}


def test_a_tdd_task_with_no_test_command_pauses_before_calling_the_implementer(make_harness):
    harness = make_harness({"implementer": []}, plan=calc_plan().model_copy(update={"test_cmd": None}))
    start(harness, "")

    escalation = harness.graph.get_state(harness.thread).values["escalation"]
    assert escalation["reason"] == "no_test_cmd"
    assert escalation["options"] == ["retry", "skip", "abort"]
    assert escalation["summary"] == "CALC-001 needs a test command: set [project] test_cmd in your config, then retry"
    assert harness.factory.calls == []


def test_a_switched_test_command_rebaselines_once_in_a_temporary_worktree(make_harness, calc_repo):
    harness = make_harness(
        {
            "implementer": [write_red, bad_green, bad_green, bad_green, write_green],
            "tester": [tester_report()],
            "reviewer": [review()],
        }
    )
    harness.start()
    assert harness.graph.get_state(harness.thread).values["escalation"]["reason"] == "attempts"

    harness.graph.update_state(harness.thread, {"test_cmd": NEW_CMD, "rebaseline": True})
    final = harness.resume({"action": "retry"})

    assert final["status"] == "completed"
    assert final["test_cmd"] == NEW_CMD
    assert final["rebaseline"] is False
    rebaselines = [name for name in logs(harness) if name.startswith("rebaseline")]
    assert len(rebaselines) == 1
    text = (harness.deps.artifacts.run_dir / "logs" / rebaselines[0]).read_text()
    assert "1 passed" in text  # the base commit's suite, not the worktree's
    worktrees = run_git(calc_repo, "worktree", "list", "--porcelain")
    assert "rebaseline" not in worktrees
    assert not any(path.name.endswith("-rebaseline") for path in harness.deps.worktree.parent.iterdir())


def test_the_rebaseline_resets_the_baseline_fields(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    harness.start()
    # A baseline the new command has to replace: a stale failure and counts from the old command.
    harness.graph.update_state(
        harness.thread,
        {"test_cmd": NEW_CMD, "rebaseline": True, "baseline_failures": ["stale::test"], "initial_baseline": ["stale::test"],
         "base_passed": 99, "base_skipped": 7},
    )
    update = harness.engine._rebaseline(harness.graph.get_state(harness.thread).values)
    assert update == {
        "baseline_failures": [],
        "initial_baseline": [],
        "base_passed": 1,
        "base_skipped": 0,
        "rebaseline": False,
    }
    assert harness.engine._rebaseline({**harness.graph.get_state(harness.thread).values, "rebaseline": False}) == {}


def test_aborting_after_a_switch_skips_the_rebaseline(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    harness.start()
    harness.graph.update_state(harness.thread, {"test_cmd": NEW_CMD, "rebaseline": True})
    final = harness.resume({"action": "abort"})
    assert final["status"] == "aborted"
    assert not [name for name in logs(harness) if name.startswith("rebaseline")]


# --- a switch after the tester committed a failing test ---------------------------------------------

MULTIPLY = Task(id="CALC-002", description="Add multiply", acceptance_criteria=["multiply(2, 3) == 6"], files_hint=["calc.py"])


def commits_a_failing_test(turn: Turn):
    (turn.workdir / "tests" / "test_known_bug.py").write_text("def test_known_bug():\n    assert False\n")
    return tester_report()


def red_multiply(turn: Turn) -> TaskResult:
    (turn.workdir / "tests" / "test_mul.py").write_text(
        "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
    )
    return task_result("red", ["tests/test_mul.py"], ["tests/test_mul.py"])


def red_that_passes(turn: Turn) -> TaskResult:
    (turn.workdir / "tests" / "test_mul.py").write_text("def test_nothing():\n    assert True\n")
    return task_result("red", ["tests/test_mul.py"], ["tests/test_mul.py"])


def green_multiply(turn: Turn) -> TaskResult:
    calc = turn.workdir / "calc.py"
    calc.write_text(calc.read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")
    return task_result("green", ["calc.py"])


def bad_green_multiply(turn: Turn) -> TaskResult:
    calc = turn.workdir / "calc.py"
    calc.write_text(calc.read_text() + "\n\ndef multiply(a, b):\n    return a + b\n")
    return task_result("green", ["calc.py"])


def task_and_run_tester():
    from phil.config import PhilConfig

    return PhilConfig.model_validate({"run": {"tester_mode": "task+run"}})


def test_after_a_switch_the_green_gate_is_not_blamed_for_the_testers_committed_failure(make_harness):
    harness = make_harness(
        {
            "implementer": [write_red, write_green, red_multiply, bad_green_multiply, bad_green_multiply,
                            bad_green_multiply, green_multiply],
            "tester": [commits_a_failing_test, tester_report(), tester_report()],
            "reviewer": [review()],
        },
        plan=calc_plan(MULTIPLY),
        config=task_and_run_tester(),
    )
    harness.start()
    paused = harness.graph.get_state(harness.thread).values
    assert (paused["escalation"]["task_id"], paused["escalation"]["phase"]) == ("CALC-002", "green")

    harness.graph.update_state(harness.thread, {"test_cmd": NEW_CMD, "rebaseline": True})
    final = harness.resume({"action": "retry"})

    assert final["status"] == "completed"
    assert [task.status for task in load_plan(final).tasks] == ["DONE", "DONE"]
    assert "tests/test_known_bug.py::test_known_bug" in final["baseline_failures"]
    assert "tests/test_known_bug.py::test_known_bug" not in final["initial_baseline"]


def test_after_a_switch_red_is_not_satisfied_by_the_testers_committed_failure(make_harness):
    harness = make_harness(
        {
            "implementer": [write_red, write_green, *[red_that_passes] * 6],
            "tester": [commits_a_failing_test],
        },
        plan=calc_plan(MULTIPLY),
        config=task_and_run_tester(),
    )
    harness.start()
    assert harness.graph.get_state(harness.thread).values["escalation"]["phase"] == "red"

    harness.graph.update_state(harness.thread, {"test_cmd": NEW_CMD, "rebaseline": True})
    harness.resume({"action": "retry"})

    escalation = harness.graph.get_state(harness.thread).values["escalation"]
    assert (escalation["task_id"], escalation["phase"]) == ("CALC-002", "red")
    assert any("no new failing tests" in problem for problem in escalation["problems"])
    assert harness.factory.remaining()["implementer"] == 0


def test_the_switch_event_is_written_when_the_new_command_is_first_used(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green, write_green],
                            "tester": [tester_report()], "reviewer": [review()]})
    harness.start()
    harness.graph.update_state(harness.thread, {"test_cmd": NEW_CMD, "rebaseline": True})
    harness.resume({"action": "retry"})
    changes = [e for e in harness.deps.events.read()[0] if e["kind"] == "test_cmd_changed"]
    assert [e["cmd"] for e in changes] == [NEW_CMD]


def test_setup_accepts_a_worktree_the_worker_already_created(make_harness):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]})
    harness.engine.worktrees.create(run_id=harness.deps.run_id, base_sha=harness.base_sha, path=harness.deps.worktree)
    assert harness.start()["status"] == "completed"
