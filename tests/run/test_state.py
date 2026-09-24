from phil.contracts import Issue, Plan, Task
from phil.run.state import initial_state, issues_to_tasks, load_plan, next_todo, render_summary, with_task_status


def plan() -> Plan:
    tasks = [
        Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"]),
        Task(id="CALC-002", description="Add multiply", acceptance_criteria=["multiply(2, 3) == 6"]),
    ]
    return Plan(keyword="CALC", description="d", tasks=tasks, test_cmd="pytest")


def test_initial_state_round_trips_the_plan():
    state = initial_state("r-0001", plan(), "abc", "pytest")
    assert load_plan(state) == plan()
    assert state["original_task_ids"] == ["CALC-001", "CALC-002"]
    assert (state["call_seq"], state["approved"], state["open_issues"], state["status"]) == (0, [], [], "pending")
    assert state["initial_baseline"] == []


def test_next_todo_and_status_updates():
    current = plan()
    assert next_todo(current) == 0
    current = with_task_status(current, 0, "DONE")
    assert next_todo(current) == 1
    current = with_task_status(current, 1, "SKIPPED")
    assert next_todo(current) is None
    assert plan().tasks[0].status == "TODO"


def test_issues_become_numbered_fix_tasks():
    issues = [
        Issue(severity="major", note="subtract ignores floats", file="calc.py"),
        Issue(severity="blocker", note="missing negative test"),
    ]
    updated = issues_to_tasks(plan(), issues, "review")
    new = updated.tasks[2:]
    assert [t.id for t in new] == ["CALC-003", "CALC-004"]
    assert new[0].description == "Fix (review): subtract ignores floats"
    assert new[0].acceptance_criteria == ["subtract ignores floats"]
    assert new[0].files_hint == ["calc.py"]
    assert new[1].files_hint == []


def test_render_summary_lists_tasks_and_issues():
    current = with_task_status(with_task_status(plan(), 0, "DONE"), 1, "SKIPPED")
    text = render_summary(
        run_id="r-0001", plan=current, status="completed", branch="phil/r-0001",
        base_sha="aaaaaaaa1111", head_sha="bbbbbbbb2222",
        open_issues=[{"severity": "minor", "note": "rename helper", "file": "calc.py", "line": 3, "task_id": None}],
    )
    assert text.startswith("# Run r-0001 · CALC\n")
    assert "Status: completed · branch phil/r-0001 · aaaaaaaa..bbbbbbbb" in text
    assert "- [x] CALC-001 Add subtract" in text
    assert "- [ ] CALC-002 Add multiply (SKIPPED)" in text
    assert "- (minor) rename helper [calc.py:3]" in text
    assert "## Open issues\n- (none)" in render_summary(
        run_id="r-0001", plan=plan(), status="completed", branch="b", base_sha="a" * 12, head_sha="b" * 12, open_issues=[]
    )
