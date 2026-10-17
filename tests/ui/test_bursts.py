from phil.ui.bursts import Burst, burst_text, edited_files, rolling_lines


def end(seq, tool, summary, result="", ok=True, role="implementer", ms=0, **extra):
    return {"seq": seq, "phase": "end", "task": "CALC-001", "role": role, "tool": tool, "summary": summary,
            "result": result, "ok": ok, "detail": None, "duration_ms": ms, **extra}


def burst(*calls):
    return Burst("CALC-001", "implementer", list(calls))


def test_folded_looks_count_as_their_real_number_and_show_as_one():
    b = burst(end(1, "read_file", "read a.py"), end(2, "read_file", "read b.py"), end(3, "grep", 'grep "x" src'),
              end(4, "run_shell", "run pytest -q", "→ ok", ms=400))
    assert burst_text(b) == "implementer ran 4 tools · ran pytest -q"
    assert rolling_lines(b) == ('    look   a.py · b.py · "x" src', "    run    pytest -q  → ok · 0.4s")


def test_edited_names_three_files_then_a_count():
    edits = [end(i, "edit_file", f"edit f{i}.py", "+1 −0") for i in range(1, 6)]
    b = burst(*edits, end(6, "write_file", "write f1.py", "+3"))
    assert burst_text(b) == "implementer ran 6 tools · edited f1.py, f2.py, f3.py +2"


def test_ran_names_the_last_shell_command_cut_to_40_characters():
    long = "node build.mjs --minify --sourcemap --target=es2022 --outdir=dist"
    b = burst(end(1, "run_shell", "run npm ci"), end(2, "run_shell", f"run {long}"))
    assert burst_text(b) == f"implementer ran 2 tools · ran {long[:39]}…"


def test_a_multi_line_command_and_an_escape_stay_on_one_line():
    heredoc = "cat <<EOF > a.txt\nline one\n\x1b[31mred\x1b[0m\nEOF"
    b = burst(end(1, "run_shell", f"run {heredoc}", "→ ok\nlater"))
    [line] = rolling_lines(b)
    for text in (line, burst_text(b)):
        assert "\n" not in text and "\x1b" not in text
    assert burst_text(b).startswith("implementer ran 1 tool · ran cat <<EOF > a.txt line one")
    assert line.endswith("→ ok later")


def test_an_escape_in_an_edited_file_name_or_role_is_shown_as_a_question_mark():
    b = Burst("CALC-001", "impl\x1b[2Jementer", [end(1, "edit_file", "edit a\x1b]0;x\x07.py\nb.py", "+1")])
    text = burst_text(b)
    assert "\x1b" not in text and "\x07" not in text and "\n" not in text
    assert text == "impl?[2Jementer ran 1 tool · edited a?]0;x?.py b.py"


def test_neither_is_the_count_alone_and_one_tool_is_singular():
    assert burst_text(burst(end(1, "read_file", "read a.py"))) == "implementer ran 1 tool"


def test_a_failed_edit_is_not_an_edited_file():
    assert edited_files([end(1, "edit_file", "edit a.py", "boom", ok=False)]) == []


def test_three_or_fewer_calls_all_show():
    b = burst(end(1, "edit_file", "edit a.py", "+1 −0"), end(2, "run_shell", "run make", "→ ok"),
              end(3, "edit_file", "edit b.py", "+2 −1"))
    assert rolling_lines(b) == ("    edit   a.py  +1 −0", "    run    make  → ok", "    edit   b.py  +2 −1")


def test_more_shows_the_first_two_a_count_and_the_latest():
    calls = [end(i, "edit_file", f"edit f{i}.py") for i in range(1, 9)]
    assert rolling_lines(burst(*calls)) == ("    edit   f1.py", "    edit   f2.py", "    … 5 more", "    edit   f8.py")
    grown = rolling_lines(burst(*calls, end(9, "edit_file", "edit f9.py")))
    assert grown[2:] == ("    … 6 more", "    edit   f9.py")  # the latest is replaced and N grows


def test_sub_agent_calls_count_but_stay_out_of_the_rolling_view():
    b = burst(end(1, "edit_file", "edit a.py"), end(2, "read_file", "read t.py", sub_id=1, sub="explore"),
              end(3, "task", "sub-agent: explore"))
    assert burst_text(b) == "implementer ran 3 tools · edited a.py"
    assert rolling_lines(b) == ("    edit   a.py", "    sub-agent: explore")


def test_no_burst_has_no_rolling_lines():
    assert rolling_lines(None) == ()
    assert Burst("T", "tester").number is None
