from phil.config import PhilConfig
from phil.contracts import Issue, TesterReport
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import self_check, task_result, tester_report, write_green, write_red


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


def test_tester_tests_are_committed(make_harness, calc_repo):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [write_edge_test]})
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
    })
    final = harness.start()
    tasks = load_plan(final).tasks
    assert [(t.id, t.status) for t in tasks] == [("CALC-001", "DONE"), ("CALC-002", "DONE")]
    assert tasks[1].description == "Fix (tester): add multiply(a, b)"


def test_tester_product_changes_are_reverted(make_harness):
    def meddle(turn):
        (turn.workdir / "calc.py").write_text("broken = True\n")
        return tester_report([Issue(severity="minor", note="naming")])

    harness = make_harness({"implementer": [write_red, write_green], "tester": [meddle]})
    final = harness.start()
    assert "def subtract" in (harness.deps.worktree / "calc.py").read_text()
    notes = [issue["note"] for issue in final["open_issues"]]
    assert "naming" in notes
    assert "tester changed product files; reverted: calc.py" in notes


def test_task_plus_run_mode_audits_each_original_task(make_harness):
    config = PhilConfig.model_validate({"run": {"tester_mode": "task+run"}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report(), tester_report()]}, config=config
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert [role for role, _ in harness.factory.calls].count("tester") == 2
