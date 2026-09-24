from phil.contracts import Issue
from phil.run.state import load_plan
from tests.run.conftest import review, self_check, tester_report, write_green, write_red
from tests.run.test_engine_tester import green_multiply, red_multiply


def test_approval_finishes_the_run(make_harness):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]})
    final = harness.start()
    assert (final["status"], final["review_rounds"]) == ("completed", 1)


def test_changes_create_fix_tasks_then_second_review(make_harness):
    issue = Issue(severity="major", note="add multiply(a, b)")
    harness = make_harness({
        "implementer": [write_red, write_green, red_multiply, green_multiply],
        "tester": [tester_report()],
        "reviewer": [review("changes", [issue]), review()],
    })
    final = harness.start()
    assert final["review_rounds"] == 2
    assert [t.id for t in load_plan(final).tasks] == ["CALC-001", "CALC-002"]
    assert final["status"] == "completed"


def test_review_round_cap_leaves_issues_open(make_harness):
    first = Issue(severity="major", note="add multiply(a, b)")
    second = Issue(severity="major", note="docstrings missing", file="calc.py")
    harness = make_harness({
        "implementer": [write_red, write_green, red_multiply, green_multiply],
        "tester": [tester_report()],
        "reviewer": [review("changes", [first]), review("changes", [second])],
    })
    final = harness.start()
    assert final["review_rounds"] == 2
    assert [issue["note"] for issue in final["open_issues"]] == ["docstrings missing"]
    assert "- (major) docstrings missing [calc.py]" in (harness.deps.artifacts.run_dir / "summary.md").read_text()


def test_reviewer_sees_open_assumptions(make_harness):
    def red_with_assumption(turn):
        result = write_red(turn)
        return result.model_copy(update={"self_check": self_check().model_copy(update={"assumptions": ["ints only"]})})

    harness = make_harness({
        "implementer": [red_with_assumption, write_green], "tester": [tester_report()], "reviewer": [review()],
    })
    harness.start()
    review_packet = [p for role, p in harness.factory.calls if role == "reviewer"][0]["messages"][0]["content"]
    assert "ints only" in review_packet
