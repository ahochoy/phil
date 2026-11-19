from rich.cells import cell_len
from phil.ui.diff_view import DIFF_FILES, DIFF_LINES, changed_lines, file_blocks

EDIT = "--- before\n+++ after\n@@ -1,3 +1,3 @@\n keep\n-old\n-- dashes\n+new\n keep2"

def test_edit_detail_keeps_only_changed_lines_and_reads_markers_by_first_char():
    assert changed_lines("edit_file", EDIT) == ([("−", "old"), ("−", "- dashes"), ("+", "new")], False)

def test_write_detail_is_all_added_and_never_parsed_as_diff():
    assert changed_lines("write_file", "+x\n--- y\n") == ([("+", "+x"), ("+", "--- y")], False)

def test_truncation_marker_is_dropped_and_reported():
    lines, cut = changed_lines("write_file", "a\n… (12 more characters not shown)")
    assert lines == [("+", "a")] and cut

def _edit(seq, path, result="+1 −1", ok=True):
    return {"seq": seq, "tool": "edit_file", "summary": f"edit {path}", "result": result,
            "ok": ok, "phase": "end", "detail": f"{seq}.txt"}

def test_header_sums_a_files_edits_and_lines_are_indented_and_styled():
    calls = [_edit(1, "a.py", "+2 −1"), _edit(2, "a.py", "+1 −0")]
    details = {1: EDIT, 2: "--- before\n+++ after\n@@ -0,0 +1 @@\n+more"}.get
    out = file_blocks(calls, details, 80, more_hint=True, number=2)
    assert out[0].plain == "        a.py  +3 −1"
    assert out[1].plain == "          − old"
    assert out[1].spans[0].style == "phil.diff.del" or out[1].style == "phil.diff.del"
    assert [t.plain.strip() for t in out[1:]] == ["− old", "− - dashes", "+ new", "+ more"]

def test_more_than_eight_lines_ends_with_a_count():
    body = "\n".join(f"line {n}" for n in range(12))
    call = {"seq": 3, "tool": "write_file", "summary": "write b.py", "result": "+12", "ok": True, "phase": "end", "detail": "3.txt"}
    out = file_blocks([call], {3: body}.get, 80, more_hint=True, number=3)
    assert out[0].plain == "        b.py  +12 −0"
    assert len(out) == 1 + DIFF_LINES + 1 and out[-1].plain == "          … 4 more lines"

def test_a_truncated_detail_points_at_more():
    call = {"seq": 4, "tool": "write_file", "summary": "write c.py", "result": "+1", "ok": True, "phase": "end", "detail": "4.txt"}
    out = file_blocks([call], {4: "a\n… (9 more characters not shown)"}.get, 80, more_hint=True, number=4)
    assert out[-1].plain == "          … more in /more #4"
    out = file_blocks([call], {4: "a\n… (9 more characters not shown)"}.get, 80, more_hint=False, number=4)
    assert out[-1].plain == "          … more"

def test_more_than_three_files_ends_with_a_files_line():
    calls = [_edit(n, f"f{n}.py") for n in range(1, 6)]
    out = file_blocks(calls, lambda seq: None, 80, more_hint=True, number=5)
    assert [t.plain for t in out if not t.plain.startswith("          ")][-1] == "        +2 more files · /more #5"
    assert file_blocks(calls, lambda seq: None, 80, more_hint=False, number=5)[-1].plain == "        +2 more files"

def test_failed_edits_and_non_edits_are_left_out():
    calls = [_edit(1, "a.py", ok=False), {"seq": 2, "tool": "read_file", "summary": "read a.py", "phase": "end"}]
    assert file_blocks(calls, {1: EDIT}.get, 80, more_hint=True, number=2) == []

def test_missing_detail_keeps_the_header():  # Review Focus 2
    out = file_blocks([_edit(1, "a.py")], lambda seq: None, 80, more_hint=True, number=1)
    assert [t.plain for t in out] == ["        a.py  +1 −1"]

def test_narrow_width_cuts_every_line():  # Review Focus 1
    out = file_blocks([_edit(1, "a_long_file_name.py")], {1: EDIT}.get, 12, more_hint=True, number=1)
    assert out and all(cell_len(t.plain) <= 11 for t in out)

def test_wide_chars_and_escapes_stay_inside_the_width():  # Review Focus 3
    detail = "--- before\n+++ after\n@@ -1 +1 @@\n+" + "漢字" * 40 + "\t\x1b[2J"
    out = file_blocks([_edit(1, "a.py")], {1: detail}.get, 40, more_hint=True, number=1)
    assert all(cell_len(t.plain) <= 39 for t in out) and "\x1b" not in out[1].plain and "\t" not in out[1].plain
