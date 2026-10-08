from rich.cells import cell_len

from phil.agents.failures import Failure
from phil.chat.decision import Decision, Option
from phil.ui.callout import KEY_HINT, callout_lines, failure_lines


def text(lines):
    return ["".join(t for _, t in line) for line in lines]


D = Decision("approval", "⏸ r-1 needs you · approve a command", ("CALC-002 wants to run:", "  node build.mjs"),
             (Option("Approve for this run", "approve"), Option("Deny: the agent continues without it", "deny"),
              Option("Abort the run", "abort")))


def test_live_box_has_a_border_the_highlight_and_the_key_hint():
    lines = text(callout_lines(D, 1, 80, live=True))
    assert lines[0].startswith("╭") and lines[-1].startswith("╰")
    assert any("⏸ r-1 needs you · approve a command" in l for l in lines)
    assert any("› 2 Deny: the agent continues without it" in l for l in lines)
    assert any("  1 Approve for this run" in l for l in lines)
    assert any(KEY_HINT.format(n=3) in l for l in lines)
    assert all(cell_len(l) <= 79 for l in lines)


def test_line_box_is_numbered_without_marker_or_hint():
    lines = text(callout_lines(D, 0, 80, live=False))
    assert any("1 Approve for this run" in l for l in lines)
    assert not any("›" in l for l in lines) and not any("↑/↓" in l for l in lines)


def test_narrow_box_has_no_border_and_fits():
    lines = text(callout_lines(D, 0, 30, live=True))
    assert not lines[0].startswith("╭")
    assert all(cell_len(l) <= 29 for l in lines)


def test_long_text_wraps_in_the_body_and_cuts_in_options():
    long = Decision("question", "Q", ("word " * 60,), (Option("x" * 200, "1"),))
    lines = text(callout_lines(long, 0, 60, live=True))
    assert all(cell_len(l) <= 59 for l in lines)
    assert any("…" in l for l in lines)


def test_markup_like_text_is_literal():
    d = Decision("question", "[bold]Q[/bold]", ("[red]x[/red]",), (Option("[link]a", "1"),))
    lines = text(callout_lines(d, 0, 80, live=True))
    assert any("[bold]Q[/bold]" in l for l in lines) and any("[red]x[/red]" in l for l in lines)


def test_option_detail_is_a_second_line():
    d = Decision("question", "Pick", (), (Option("Inline (recommended)", "1", detail="Add it to calc.py."),))
    lines = text(callout_lines(d, 0, 80, live=True))
    assert any("Add it to calc.py." in l for l in lines)


def test_failure_box():
    f = Failure("quota", "Your openrouter account is out of credits.", "Phil won't retry this.", "Add credits, then try again.")
    lines = text(failure_lines(f, 80))
    joined = "\n".join(lines)
    assert "✗ Your openrouter account is out of credits." in joined
    assert "Phil won't retry this." in joined and "Add credits, then try again." in joined
    assert "Details: /more 1" in joined
