import pytest
from rich.cells import cell_len

from phil.agents.failures import Failure
from phil.chat.decision import BODY_LINES, Decision, Option, pause_decision
from phil.ui.callout import KEY_HINT, callout_lines, failure_lines, overflows


def text(lines):
    return ["".join(t for _, t in line) for line in lines]


D = Decision("approval", "⏸ r-1 needs you · approve a command", ("CALC-002 wants to run:", "  node build.mjs"),
             (Option("Approve for this run", "approve"), Option("Deny: the agent continues without it", "deny"),
              Option("Abort the run", "abort")))

D_CJK = Decision(
    "question", "⏸ r-1 需要你处理一个重要的决定",
    ("这是一条用来测试换行是否正确的很长的中文说明文字。",),
    (Option("继续执行这个任务", "1"), Option("取消并中止这次运行", "2")),
)

D_EMOJI = Decision(
    "confirm", "🚀 Ready to deploy? 🎉",
    ("This will deploy to production 🔥 and cannot be undone 💥.",),
    (Option("Yes, deploy now 🚀", "y"), Option("No, cancel ❌", "n")),
)

F = Failure("quota", "Your openrouter account is out of credits.", "Phil won't retry this.",
            "Add credits, then try again.")

WIDTHS = (1, 5, 10, 20, 30, 40, 80)


def test_live_box_has_a_border_the_highlight_and_the_key_hint():
    lines = text(callout_lines(D, 1, 80, live=True))
    assert lines[0].startswith("╭") and lines[-1].startswith("╰")
    assert any("⏸ r-1 needs you · approve a command" in l for l in lines)
    assert any("› 2 Deny: the agent continues without it" in l for l in lines)
    assert any("  1 Approve for this run" in l for l in lines)
    assert any(KEY_HINT.format(n=3) in l for l in lines)
    assert all(cell_len(l) <= 79 for l in lines)


def test_the_key_hint_is_the_specs_string():
    lines = text(callout_lines(D, 0, 80, live=True))
    assert any("↑/↓ choose · Enter confirm · 1–3 · Esc type a message" in l for l in lines)


def test_the_key_hint_stops_at_9_number_keys():
    many = Decision("question", "Pick", (), tuple(Option(f"option {n}", str(n)) for n in range(1, 13)))
    lines = text(callout_lines(many, 0, 80, live=True))
    assert any("↑/↓ choose · Enter confirm · 1–9 · Esc type a message" in l for l in lines)


def test_the_body_cap_counts_wrapped_rows():
    """A pause with two 400-character problems at width 60: at most BODY_LINES body rows, the
    last one "… see /more 1", so the docked box stays short enough for a 24-row terminal."""
    problems = [" ".join(["problem"] * 50), " ".join(["another"] * 50)]
    pause = pause_decision("r-1", {"reason": "review_failed", "summary": "no review", "problems": problems,
                                   "options": ["retry", "abort"]})
    assert all(len(p) >= 399 for p in problems) and "… see /more 1" not in pause.body
    lines = text(callout_lines(pause, 0, 60, live=True))
    rows = [l.strip("│ ").rstrip() for l in lines[1:-1]]
    body = rows[1:rows.index("")]  # after the title, up to the blank row before the options
    assert len(body) == BODY_LINES
    assert body[-1] == "… see /more 1"
    assert overflows(pause, 60) and not overflows(D, 60)


def test_the_see_more_line_and_the_callout_styles_have_one_source():
    """"… see /more 1" is defined once (decision.SEE_MORE); the unused phil.callout.key style is gone."""
    from phil.chat import controller, decision
    from phil.ui import callout
    from phil.ui.theme import STYLE_NAMES, prompt_toolkit_styles

    assert controller.SEE_MORE is decision.SEE_MORE and callout.SEE_MORE is decision.SEE_MORE
    assert "phil.callout.key" not in STYLE_NAMES and "phil.callout.key" not in prompt_toolkit_styles()


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


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("decision", (D, D_CJK, D_EMOJI), ids=("ascii", "cjk", "emoji"))
@pytest.mark.parametrize("live", (True, False))
def test_every_line_fits_the_box_at_its_width(decision, live, width):
    """A bordered or indented row always costs at least 2 cells of overhead (the border's
    "│ "/" │" or the narrow indent "  "), on top of an inner width that's floored at 1 cell
    when the box is this narrow. At width 1 that overhead alone already exceeds the box, so
    no line can fit inside `width - 1` cells there; the only thing that must hold is that
    building the box doesn't raise. From width 5 up, the floor never bites and every line's
    cell width is exactly `width - 1` once padded, so `cell_len(line) <= width - 1` holds."""
    lines = text(callout_lines(decision, 0, width, live=live))
    if width == 1:
        return
    bound = width - 1
    assert all(cell_len(l) <= bound for l in lines)


@pytest.mark.parametrize("width", WIDTHS)
def test_failure_box_fits_every_width(width):
    lines = text(failure_lines(F, width))
    if width == 1:
        return
    assert all(cell_len(l) <= width - 1 for l in lines)


def test_body_line_keeps_its_leading_indent():
    """The approval's second body line, "  node build.mjs", keeps its two-space indent on
    every wrapped line; a bordered row only ever contributes a single space of its own
    (the "│ " before the content), so this substring only appears if the indent survived."""
    lines = text(callout_lines(D, 0, 80, live=True))
    assert any("  node build.mjs" in l for l in lines)
