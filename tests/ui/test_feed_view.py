import pytest
from rich.cells import cell_len

from phil.ui.feed_view import BURST_LINES, FeedRenderer


def end(seq, tool, summary, result="", detail=None, task="CALC-001", role="implementer", ms=10):
    return {"seq": seq, "phase": "end", "task": task, "role": role, "tool": tool, "summary": summary,
            "result": result, "ok": True, "detail": detail, "duration_ms": ms}


def plain(lines):
    return [line.plain for line in lines]


def test_tool_lines_are_indented_with_verb_result_duration_and_ref():
    lines = FeedRenderer().tool_lines([
        end(13, "edit_file", "edit calc.py", "+4 −1", "13.txt"),
        end(14, "run_shell", "run pytest -q", "→ 2 failed", "14.txt", ms=1800),
    ], width=120)
    assert plain(lines) == ["    edit  calc.py  +4 −1  #13", "    run   pytest -q  → 2 failed · 1.8s  #14"]


def test_consecutive_reads_fold_into_one_line():
    lines = FeedRenderer().tool_lines([end(1, "read_file", "read calc.py"), end(2, "read_file", "read tests/t.py")], 120)
    assert plain(lines) == ["    read  calc.py · tests/t.py"]


def test_reads_in_different_tasks_do_not_fold():
    lines = FeedRenderer().tool_lines([end(1, "read_file", "read a.py"), end(2, "read_file", "read b.py", task="T2")], 120)
    assert len(lines) == 2


def test_start_records_are_ignored():
    assert FeedRenderer().tool_lines([{"seq": 1, "phase": "start", "summary": "run x"}], 120) == []


def test_a_narrow_line_never_wraps_and_keeps_its_ref():
    long = "run " + "x" * 200
    (line,) = FeedRenderer().tool_lines([end(7, "run_shell", long, "→ 1 failed", "7.txt", ms=900)], width=40)
    assert len(line.plain) <= 39 and line.plain.endswith("#7") and "…" in line.plain


def test_a_burst_collapses_past_the_limit():
    records = [end(i, "edit_file", f"edit f{i}.py", "+1 −0", f"{i}.txt") for i in range(1, BURST_LINES + 8)]
    lines = plain(FeedRenderer().tool_lines(records, 120))
    assert len(lines) == BURST_LINES + 1 and lines[-1] == "    … 7 more steps"


def test_a_burst_of_searches_says_steps():
    records = [end(i, "grep", f'grep "x" f{i}', task=f"T{i}") for i in range(1, BURST_LINES + 4)]
    assert plain(FeedRenderer().tool_lines(records, 120))[-1] == "    … 3 more steps"


def test_a_burst_of_reads_says_reads():
    records = [end(i, "read_file", f"read f{i}.py", task=f"T{i}") for i in range(1, BURST_LINES + 4)]
    assert plain(FeedRenderer().tool_lines(records, 120))[-1] == "    … 3 more reads"


def test_milestones():
    feed = FeedRenderer()
    assert feed.milestone({"kind": "task_started", "task": "CALC-001", "title": "Add multiply", "ts": "2026-10-07T10:00:00Z"}, 120).plain == "▸ CALC-001 Add multiply"
    assert feed.milestone({"kind": "gate", "task": "CALC-001", "name": "red"}, 120).plain == "✓ CALC-001 red: the new tests fail as expected"
    assert feed.milestone({"kind": "gate", "task": "CALC-001", "name": "green"}, 120).plain == "✓ CALC-001 green: tests pass"
    assert feed.milestone({"kind": "attempt_failed", "task": "CALC-001", "attempt": 1, "limit": 3, "problem": "2 tests failed", "retrying": True}, 120).plain == "✗ CALC-001 attempt 1 failed: 2 tests failed · retrying (2 of 3)"
    assert feed.milestone({"kind": "attempt_failed", "task": "CALC-001", "attempt": 3, "limit": 3, "problem": "x", "retrying": False}, 120).plain == "✗ CALC-001 attempt 3 failed: x · needs you"
    assert feed.milestone({"kind": "task_done", "task": "CALC-001", "files": 2, "ts": "2026-10-07T10:00:41Z"}, 120).plain == "✓ CALC-001 done · 2 files · 41s"
    assert feed.milestone({"kind": "verdict", "role": "reviewer", "outcome": "passed", "issues": 0, "blocking": 0}, 120).plain == "✓ reviewer approved"
    assert feed.milestone({"kind": "verdict", "role": "reviewer", "outcome": "changes", "issues": 3, "blocking": 1}, 120).plain == "✗ reviewer asked for changes: 3 issues (1 blocking)"
    assert feed.milestone({"kind": "verdict", "role": "tester", "outcome": "passed", "issues": 0, "blocking": 0}, 120).plain == "✓ tester: no issues"


def test_task_done_without_a_known_start_omits_the_elapsed_time():
    assert FeedRenderer().milestone({"kind": "task_done", "task": "T9", "files": 1, "ts": "2026-10-07T10:00:00Z"}, 120).plain == "✓ T9 done · 1 file"


def test_task_done_with_files_none_omits_the_file_count():
    """task_done.files can be None when counting failed; render it as "✓ T done" with no count."""
    assert FeedRenderer().milestone({"kind": "task_done", "task": "T5", "files": None, "ts": "2026-10-07T10:00:00Z"}, 120).plain == "✓ T5 done"


def test_task_done_with_files_none_still_shows_elapsed_time_when_known():
    feed = FeedRenderer()
    feed.milestone({"kind": "task_started", "task": "T6", "title": "x", "ts": "2026-10-07T10:00:00Z"}, 120)
    assert feed.milestone({"kind": "task_done", "task": "T6", "files": None, "ts": "2026-10-07T10:00:41Z"}, 120).plain == "✓ T6 done · 41s"


def test_a_milestone_with_task_none_never_renders_the_word_none():
    """A record's task can be None (e.g. the tester or reviewer at the end of a run); it must
    render as empty, not the literal string "None"."""
    feed = FeedRenderer()
    assert "None" not in feed.milestone({"kind": "gate", "task": None, "name": "green"}, 120).plain
    assert "None" not in feed.milestone({"kind": "task_started", "task": None, "title": "x", "ts": "2026-10-07T10:00:00Z"}, 120).plain


def test_a_failed_tool_line_shows_its_result_in_the_fail_style():
    """A tool line whose record has ok: False shows its result in phil.gate.fail, not muted."""
    record = end(5, "run_shell", "run pytest -q", "→ 2 failed", "5.txt", ms=1800)
    record["ok"] = False
    (line,) = FeedRenderer().tool_lines([record], 120)
    fail_spans = [span for span in line.spans if span.style == "phil.gate.fail"]
    assert len(fail_spans) == 1
    span = fail_spans[0]
    assert line.plain[span.start:span.end] == "  → 2 failed"
    # the duration after the result, and the rest of the line, stay muted rather than failed
    assert all(span.style != "phil.gate.fail" for span in line.spans if line.plain[span.start:span.end] != "  → 2 failed")


def test_a_passing_tool_line_keeps_its_result_muted():
    record = end(6, "run_shell", "run pytest -q", "→ 2 passed", "6.txt")
    (line,) = FeedRenderer().tool_lines([record], 120)
    assert not any(span.style == "phil.gate.fail" for span in line.spans)


@pytest.mark.parametrize("width", [20, 40, 120])
@pytest.mark.parametrize(
    "record",
    [
        end(1, "edit_file", "x" * 300),  # a spaceless summary: no clean short verb
        end(2, "edit_file", "edit " + "文件" * 50),  # wide characters in the body
        end(3, "run_shell", "run " + "y" * 300, result="→ 1 failed", detail="x.txt", ms=900),  # result + ref
    ],
    ids=["spaceless", "wide-chars", "run-with-result-and-ref"],
)
def test_a_line_never_exceeds_the_width(record, width):
    """A tool line must fit `width - 1` cells at every width, whatever the summary looks like,
    and a kept ref must still end the line as `#n`."""
    (line,) = FeedRenderer().tool_lines([record], width)
    assert cell_len(line.plain) <= width - 1
    if record.get("detail"):
        assert line.plain.endswith(f"#{record['seq']}")


def test_two_reads_with_task_none_fold_into_one_line():
    """Records with task: None fold and group correctly (None == None)."""
    r1 = end(1, "read_file", "read a.py", task=None, role="tester")
    r2 = end(2, "read_file", "read b.py", task=None, role="tester")
    lines = FeedRenderer().tool_lines([r1, r2], 120)
    assert plain(lines) == ["    read  a.py · b.py"]
