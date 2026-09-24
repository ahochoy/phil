import pytest

from phil.config import PhilConfig
from phil.contracts import Issue, Task, TesterReport
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import calc_plan, review, self_check, task_result, tester_report, write_green, write_red


def write_edge_test(turn):
    (turn.workdir / "tests" / "test_edge.py").write_text(
        "from calc import subtract\n\n\ndef test_negative():\n    assert subtract(1, 3) == -2\n"
    )
    return TesterReport(tests_added=["tests/test_edge.py"], issues=[], self_check=self_check())


def red_multiply(turn):
    (turn.workdir / "tests" / "test_mul.py").write_text(
        "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
    )
    return task_result("red", ["tests/test_mul.py"], ["tests/test_mul.py"])


def green_multiply(turn):
    calc = turn.workdir / "calc.py"
    calc.write_text(calc.read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")
    return task_result("green", ["calc.py"])


def failing_edge_test(turn):
    (turn.workdir / "tests" / "test_edge.py").write_text(
        "from calc import add\n\n\ndef test_add_strings():\n    assert add('a', 1) == 'a1'\n"
    )
    return TesterReport(
        tests_added=["tests/test_edge.py"],
        issues=[Issue(severity="major", note="add() should concatenate str and int")],
        self_check=self_check(),
    )


def write_red_fix(turn):
    (turn.workdir / "tests" / "test_add_str.py").write_text(
        "from calc import add\n\n\ndef test_add_str():\n    assert add('x', 2) == 'x2'\n"
    )
    return task_result("red", ["tests/test_add_str.py"], ["tests/test_add_str.py"])


def write_green_fix(turn):
    calc = turn.workdir / "calc.py"
    content = calc.read_text()
    content = content.replace("    return a + b", '    return f"{a}{b}" if isinstance(a, str) else a + b', 1)
    calc.write_text(content)
    return task_result("green", ["calc.py"])


def test_tester_tests_are_committed(make_harness, calc_repo):
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [write_edge_test], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert final["tester_done"] is True
    subjects = run_git(calc_repo, "log", "--format=%s", "phil/r-0001").splitlines()
    assert subjects[0] == "CALC: tests from tester"
    assert (harness.deps.worktree / "tests" / "test_edge.py").exists()


def test_major_issue_becomes_a_fix_task(make_harness):
    issue = Issue(severity="major", note="add multiply(a, b)", file="calc.py")
    harness = make_harness({
        "implementer": [write_red, write_green, red_multiply, green_multiply],
        "tester": [tester_report([issue])],
        "reviewer": [review()],
    })
    final = harness.start()
    tasks = load_plan(final).tasks
    assert [(t.id, t.status) for t in tasks] == [("CALC-001", "DONE"), ("CALC-002", "DONE")]
    assert tasks[1].description == "Fix (tester): add multiply(a, b)"


def test_tester_product_changes_are_reverted(make_harness):
    def meddle(turn):
        (turn.workdir / "calc.py").write_text("broken = True\n")
        return tester_report([Issue(severity="minor", note="naming")])

    harness = make_harness({"implementer": [write_red, write_green], "tester": [meddle], "reviewer": [review()]})
    final = harness.start()
    assert "def subtract" in (harness.deps.worktree / "calc.py").read_text()
    notes = [issue["note"] for issue in final["open_issues"]]
    assert "naming" in notes
    assert "tester changed product files; reverted: calc.py" in notes


def test_task_plus_run_mode_audits_each_original_task(make_harness):
    config = PhilConfig.model_validate({"run": {"tester_mode": "task+run"}})
    harness = make_harness(
        {
            "implementer": [write_red, write_green],
            "tester": [tester_report(), tester_report()],
            "reviewer": [review()],
        },
        config=config,
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert [role for role, _ in harness.factory.calls].count("tester") == 2


def test_tester_failures_do_not_fail_later_tasks(make_harness):
    multiply = Task(id="CALC-002", description="Add multiply", acceptance_criteria=["multiply(2, 3) == 6"])
    config = PhilConfig.model_validate({"run": {"tester_mode": "task+run"}})
    harness = make_harness(
        {
            "implementer": [write_red, write_green, red_multiply, green_multiply, write_red_fix, write_green_fix],
            "tester": [failing_edge_test, tester_report(), tester_report()],
            "reviewer": [review()],
        },
        plan=calc_plan(multiply),
        config=config,
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert [(t.id, t.status) for t in load_plan(final).tasks][:2] == [("CALC-001", "DONE"), ("CALC-002", "DONE")]
    assert "tests/test_edge.py::test_add_strings" in final["baseline_failures"]


def test_tester_resumes_cleanly_after_a_crash(make_harness):
    def crash_after_writing(turn):
        (turn.workdir / "tests" / "test_edge.py").write_text("x = 1\n")
        raise RuntimeError("process died")

    harness = make_harness(
        {
            "implementer": [write_red, write_green],
            "tester": [crash_after_writing, tester_report()],
            "reviewer": [review()],
        }
    )
    with pytest.raises(RuntimeError, match="process died"):
        harness.start()
    final = harness.graph.invoke(None, harness.thread)
    assert final["status"] == "completed"
    assert not (harness.deps.worktree / "tests" / "test_edge.py").exists()


def test_rejected_tester_output_is_a_major_open_issue(make_harness):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [{}, {}], "reviewer": [review()]})
    final = harness.start()
    assert final["status"] == "completed"
    rejected = [issue for issue in final["open_issues"] if issue["note"].startswith("tester output rejected")]
    assert [issue["severity"] for issue in rejected] == ["major"]


def test_refused_tester_commands_are_reported(make_harness):
    def forbidden(turn):
        turn.tools["run_shell"]("ls | cat")
        return tester_report()

    harness = make_harness({"implementer": [write_red, write_green], "tester": [forbidden], "reviewer": [review()]})
    final = harness.start()
    assert "tester command refused: ls | cat" in [issue["note"] for issue in final["open_issues"]]


def test_new_failures_at_finish_become_major_open_issues(make_harness):
    def failing_edge_test_minor(turn):
        (turn.workdir / "tests" / "test_edge.py").write_text(
            "from calc import add\n\n\ndef test_add_strings():\n    assert add('a', 1) == 'a1'\n"
        )
        return tester_report([Issue(severity="minor", note="add() rejects mixed types")])

    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [failing_edge_test_minor], "reviewer": [review()]}
    )
    final = harness.start()
    assert final["status"] == "completed"
    summary = (harness.deps.artifacts.run_dir / "summary.md").read_text()
    assert "- (major) still failing: tests/test_edge.py::test_add_strings" in summary
    assert "- (minor) add() rejects mixed types" in summary
