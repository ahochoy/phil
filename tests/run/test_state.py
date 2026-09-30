from phil.contracts import Issue, Plan, Task
from phil.run.state import (
    clean_note,
    dedupe_issues,
    initial_state,
    issues_to_tasks,
    load_plan,
    next_todo,
    render_summary,
    with_task_status,
)
from phil.store.telemetry import Totals, UsageLine


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
    updated = issues_to_tasks(plan(), issues, "review", "pytest")
    new = updated.tasks[2:]
    assert [t.verify for t in new] == ["tdd", "tdd"]
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


def test_clean_note_collapses_whitespace_and_newlines():
    assert clean_note("first line\n\nsecond   line\tthird") == "first line second line third"


def test_clean_note_strips_markdown_emphasis_and_backticks():
    assert clean_note("**bold** and __also bold__ and *italic* and _also italic_ and `code`") == (
        "bold and also bold and italic and also italic and code"
    )


def test_clean_note_strips_leading_heading_and_list_markers():
    assert clean_note("# Heading here") == "Heading here"
    assert clean_note("- list item") == "list item"
    assert clean_note("1. numbered item") == "numbered item"


def test_clean_note_does_not_corrupt_arithmetic():
    # A `*` flanked by spaces on both sides is not an emphasis delimiter (CommonMark requires
    # the char right after an opening delimiter, and right before a closing one, to be
    # non-space), so plain multiplication must survive untouched.
    assert clean_note("3 * 4 * 5") == "3 * 4 * 5"
    assert clean_note("expected 2 * 3 == 6") == "expected 2 * 3 == 6"


def test_clean_note_does_not_corrupt_double_star_exponents():
    # "x**2" is not bold (`**` isn't flanked as an opening delimiter: it's glued to the word
    # `x` on one side), so it must survive untouched.
    assert clean_note("x**2 and y**2 differ") == "x**2 and y**2 differ"


def test_clean_note_leaves_underscored_identifiers_alone():
    # Intraword underscores (both flanks are word characters) are not markdown emphasis, so an
    # identifier like `test_add_strings` must survive untouched (CommonMark's own rule for `_`).
    assert clean_note("still failing: tests/test_edge.py::test_add_strings") == (
        "still failing: tests/test_edge.py::test_add_strings"
    )


def test_clean_note_caps_to_limit_with_ellipsis():
    text = "x" * 250
    result = clean_note(text, limit=200)
    assert len(result) == 200
    assert result.endswith("…")
    assert result[:-1] == "x" * 199


def test_dedupe_issues_keeps_the_highest_severity_and_first_order():
    issues = [
        {"task_id": "CALC-001", "severity": "minor", "note": "  rename   helper  "},
        {"task_id": "CALC-002", "severity": "major", "note": "other issue"},
        {"task_id": "CALC-001", "severity": "blocker", "note": "**rename** helper"},
    ]
    result = dedupe_issues(issues)
    assert [(i["task_id"], i["severity"], i["note"]) for i in result] == [
        ("CALC-001", "blocker", "rename helper"),
        ("CALC-002", "major", "other issue"),
    ]


def test_dedupe_issues_does_not_downgrade_severity():
    issues = [
        {"task_id": "CALC-001", "severity": "blocker", "note": "note"},
        {"task_id": "CALC-001", "severity": "minor", "note": "note"},
    ]
    assert [i["severity"] for i in dedupe_issues(issues)] == ["blocker"]


def test_render_summary_appends_a_usage_section():
    usage = [
        UsageLine(
            layer="run", role="implementer", calls=6, input_tokens=150210, output_tokens=9120, cost_usd=0.33,
            model_calls=14, tool_calls={"run_shell": 9, "read_file": 12}, retries=0, cost_source="estimated",
        ),
    ]
    totals = Totals(tokens=203812, cost_usd=0.41, cost_source="estimated")
    text = render_summary(
        run_id="r-0001", plan=plan(), status="completed", branch="b", base_sha="a" * 12, head_sha="b" * 12,
        open_issues=[], usage=usage, totals=totals,
    )
    assert "## Usage" in text
    assert "Total: 203,812 tokens · ~$0.41 (estimated)" in text
    assert (
        "- run/implementer: 6 calls (14 model calls) · 150,210 in / 9,120 out · ~$0.33 · "
        "tools: run_shell×9, read_file×12" in text
    )


def test_render_summary_omits_usage_section_when_totals_not_given():
    text = render_summary(
        run_id="r-0001", plan=plan(), status="completed", branch="b", base_sha="a" * 12, head_sha="b" * 12,
        open_issues=[],
    )
    assert "## Usage" not in text


def test_render_summary_dedupes_open_issues():
    text = render_summary(
        run_id="r-0001", plan=plan(), status="completed", branch="b", base_sha="a" * 12, head_sha="b" * 12,
        open_issues=[
            {"task_id": None, "severity": "minor", "note": "rename helper"},
            {"task_id": None, "severity": "blocker", "note": "**rename**   helper"},
        ],
    )
    assert text.count("rename helper") == 1
    assert "- (blocker) rename helper" in text


def check_only_plan(test_cmd: str | None = "pytest") -> Plan:
    tasks = [
        Task(id="CALC-001", description="Copy", acceptance_criteria=["c"], verify="check", check_cmd="grep -q a x"),
        Task(id="CALC-002", description="Copy", acceptance_criteria=["c"], verify="check", check_cmd="grep -q b y"),
    ]
    return Plan(keyword="CALC", description="d", tasks=tasks, test_cmd=test_cmd)


def test_findings_in_an_all_check_run_become_check_tasks_with_the_first_check_cmd():
    issue = Issue(severity="major", note="title missing")
    new = issues_to_tasks(check_only_plan(), [issue], "review", "pytest").tasks[2]
    assert (new.verify, new.check_cmd) == ("check", "grep -q a x")
    no_suite = issues_to_tasks(check_only_plan(None), [issue], "tester", "").tasks[2]
    assert (no_suite.verify, no_suite.check_cmd) == ("check", "grep -q a x")


def test_findings_in_a_run_without_a_test_command_become_check_tasks():
    mixed = plan().model_copy(
        update={"tasks": [*plan().tasks, check_only_plan().tasks[1].model_copy(update={"id": "CALC-003"})]}
    )
    new = issues_to_tasks(mixed, [Issue(severity="major", note="n")], "review", "").tasks[3]
    assert (new.verify, new.check_cmd) == ("check", "grep -q b y")
    # A mixed run with a test command keeps its findings tdd.
    assert issues_to_tasks(mixed, [Issue(severity="major", note="n")], "review", "pytest").tasks[3].verify == "tdd"


def test_findings_stay_tdd_when_no_check_cmd_exists_to_borrow():
    assert issues_to_tasks(plan(), [Issue(severity="major", note="n")], "review", "").tasks[2].verify == "tdd"
