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
    assert load_plan(final).tasks[1].verify == "tdd"
    assert load_plan(final).tasks[1].check_cmd is None
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


def failed_review_harness(make_harness, reviewers):
    return make_harness({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": reviewers})


def test_failed_review_escalates(make_harness):
    harness = failed_review_harness(make_harness, [{}, {}])
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "review_failed"
    assert escalation["options"] == ["retry", "finish", "abort"]
    assert escalation["resume_to"] == "review"
    assert escalation["summary"] == "reviewer did not return a valid review"
    assert escalation["problems"]


def test_failed_review_retry_then_completes(make_harness):
    harness = failed_review_harness(make_harness, [{}, {}, review()])
    harness.start()
    final = harness.resume({"action": "retry"})
    assert (final["status"], final["review_rounds"]) == ("completed", 1)


def test_failed_review_finish_leaves_a_major_issue(make_harness):
    harness = failed_review_harness(make_harness, [{}, {}])
    harness.start()
    final = harness.resume({"action": "finish"})
    assert final["status"] == "incomplete"  # an unfinished review is a major issue left open
    assert {"severity": "major", "note": "review not completed"}.items() <= final["open_issues"][-1].items()
    assert "- (major) review not completed" in (harness.deps.artifacts.run_dir / "summary.md").read_text()


def finish_with(make_harness, issues):
    harness = make_harness({
        "implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review("approve", issues)],
    })
    return harness, harness.start()


def summary_header(harness) -> str:
    lines = (harness.deps.artifacts.run_dir / "summary.md").read_text().splitlines()
    return next(line for line in lines if line.startswith("Status: "))


def test_a_run_that_finishes_with_a_major_issue_open_is_incomplete(make_harness):
    harness, final = finish_with(make_harness, [Issue(severity="major", note="no docstring")])
    assert final["status"] == "incomplete"
    assert harness.run_record().state == "incomplete"
    assert summary_header(harness).startswith("Status: incomplete · 1 blocking issue(s) open · branch ")


def test_a_run_that_finishes_with_a_blocker_open_is_incomplete(make_harness):
    issues = [Issue(severity="blocker", note="Diff is empty"), Issue(severity="major", note="no tests"),
              Issue(severity="minor", note="nit")]
    harness, final = finish_with(make_harness, issues)
    assert final["status"] == "incomplete"
    assert summary_header(harness).startswith("Status: incomplete · 2 blocking issue(s) open · ")


def test_a_run_with_only_minor_issues_open_is_completed(make_harness):
    harness, final = finish_with(make_harness, [Issue(severity="minor", note="nit")])
    assert final["status"] == "completed"
    assert harness.run_record().state == "completed"
    assert summary_header(harness).startswith("Status: completed · branch ")
