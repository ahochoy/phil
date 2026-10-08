from rich.cells import cell_len

from phil.chat.state import LiveStep, RunView, SideJob, SubAgent, ToolbarView
from phil.ui.toolbar import (
    budget_style, format_tokens, render_live_rows, render_toolbar, short_model, task_dots, toolbar_text,
)

RUN = RunView(run_id="r-4f2a", keyword="calc", node="implement", tasks_done=1, tasks_total=3, started=0.0)
FULL = ToolbarView(repo="calc", branch="phil/r-4f2a", run=RUN, model=("low", "gemini-3.8-flash"),
                   tokens=182_000, run_cost=(0.41, "reported"), budget_usd=1.0)


def text(view, now=64.0, width=None):
    return toolbar_text(render_toolbar(view, now, width))


def test_short_model():
    assert short_model("openrouter:google/gemini-3.8-flash") == "gemini-3.8-flash"
    assert short_model("ollama:qwen3:8b") == "qwen3:8b"
    assert short_model("model-x") == "model-x"
    assert short_model("anthropic:claude-sonnet-5") == "claude-sonnet-5"


def test_format_tokens():
    assert format_tokens(950) == "950 tok"
    assert format_tokens(182_000) == "182k tok"
    assert format_tokens(1_250_000) == "1.2M tok"


def test_task_dots_pulse_and_pause():
    working = toolbar_text(task_dots(1, 3, now=0.0, paused=False))
    assert working == "●◉○"
    assert toolbar_text(task_dots(1, 3, now=0.5, paused=False)) == "●○○"
    paused = task_dots(1, 3, now=0.5, paused=True)
    assert toolbar_text(paused) == "●◉○" and any("phil.warn" in style for style, t in paused if t == "◉")
    assert toolbar_text(task_dots(0, 1, now=0.0, paused=False)) == "◉"
    assert toolbar_text(task_dots(13, 20, now=0.0, paused=False)) == "●●●…◉ 14/20"
    assert toolbar_text(task_dots(3, 3, now=0.0, paused=False)) == "●●●"


def test_budget_style_thresholds():
    assert budget_style(0.79, 1.0) == "class:phil.cost"
    assert budget_style(0.80, 1.0) == "class:phil.warn"
    assert budget_style(1.00, 1.0) == "class:phil.error"
    assert budget_style(5.0, 0.0) == "class:phil.cost"


def test_run_in_progress_full_width():
    assert text(FULL) == ("calc @ phil/r-4f2a │ r-4f2a ●◉○ implement · 1m 04s │ low·gemini-3.8-flash │ "
                          "182k tok │ $0.41/$1.00")


def test_idle():
    view = ToolbarView(repo="calc", branch="main", cost=(0.06, "reported"))
    assert text(view) == "calc @ main │ Phil · type a goal, or /help │ chat $0.06"


def test_working_on_a_goal():
    view = ToolbarView(repo="calc", branch="main", step="architect", step_started=52.0,
                       model=("high", "claude-sonnet-5"), cost=(0.06, "reported"))
    out = text(view)
    assert out.startswith("calc @ main │ ") and "Architect drafting · 12s │ high·claude-sonnet-5 │ chat $0.06" in out


def test_paused_shows_the_notice_and_hides_stage_elapsed_and_model():
    view = ToolbarView(repo="calc", branch="phil/r-4f2a", run=RUN, paused=True, model=("low", "x"),
                       tokens=182_000, run_cost=(0.41, "reported"), budget_usd=1.0)
    assert text(view) == "calc @ phil/r-4f2a │ ⏸ r-4f2a needs you │ r-4f2a ●◉○ │ 182k tok │ $0.41/$1.00"


def test_btw_and_parked_are_appended():
    view = ToolbarView(repo="calc", branch="main", btw_pending=2, parked=3)
    assert text(view).endswith("│ /btw ×2 │ 3 parked")


def test_no_budget_shows_cost_alone():
    view = ToolbarView(repo="calc", branch="b", run=RUN, run_cost=(0.41, "estimated"), budget_usd=0.0)
    assert text(view).endswith("│ ~$0.41")


def test_drop_order():
    # first to go: repo → model → tokens → elapsed → cost → progress; the pause notice never drops
    assert "calc @" not in text(FULL, width=80)
    w60 = text(FULL, width=60)
    assert "gemini" not in w60 and "calc @" not in w60
    w40 = text(FULL, width=40)
    assert "tok" not in w40 and "1m 04s" not in w40
    assert "r-4f2a ●◉○" in text(FULL, width=30)


def test_never_wraps_and_pause_survives():
    paused = ToolbarView(repo="calc", branch="b", run=RUN, paused=True, run_cost=(0.41, "reported"), budget_usd=1.0)
    for width in (20, 30, 40, 60, 80, 120):
        assert cell_len(text(FULL, width=width)) <= width - 1
        assert "⏸ r-4f2a needs you" in text(paused, width=max(width, 24))


def test_twelve_tasks_show_twelve_dots():
    """Exactly MAX_DOTS (12) tasks: 12 dots, no ellipsis and no count."""
    dots = toolbar_text(task_dots(4, 12, now=0.0, paused=False))
    assert dots == "●●●●◉○○○○○○○"
    assert len(dots) == 12 and "…" not in dots and "/" not in dots


def test_full_line_fits_at_120():
    """At width 120 the full run line drops nothing."""
    assert text(FULL, width=120) == text(FULL)


def test_a_cut_pause_notice_keeps_its_warning_style():
    """When only the pause notice is left and it still has to be cut, it's cut with … and stays phil.warn."""
    paused = ToolbarView(repo="calc", branch="b", run=RUN, paused=True, run_cost=(0.41, "reported"), budget_usd=1.0)
    frags = render_toolbar(paused, 64.0, 12)
    assert toolbar_text(frags).endswith("…") and cell_len(toolbar_text(frags)) <= 11
    assert toolbar_text(frags).startswith("⏸ r-4f2a")
    assert all(style == "class:phil.warn" for style, _ in frags)


def test_dropping_progress_drops_its_orphaned_elapsed(monkeypatch):
    """The plan's ruling: an elapsed segment (glued with ' · ') means nothing without the
    progress segment it's attached to. Dropping priorities (P_ELAPSED < P_PROGRESS) already keep
    this from happening, but the loop also enforces it directly — this proves that enforcement by
    forcing the unlikely order (progress outranked, so it would drop while elapsed stays) and
    checking the elapsed text doesn't survive alone."""
    import phil.ui.toolbar as toolbar_mod

    monkeypatch.setattr(toolbar_mod, "P_PROGRESS", 0)
    monkeypatch.setattr(toolbar_mod, "P_ELAPSED", 7)
    view = ToolbarView(repo="calc", branch="main", run=RUN)
    out = text(view, width=15)
    assert "r-4f2a" not in out
    assert "1m 04s" not in out  # the orphaned elapsed segment must go too


def test_toolbar_styles_are_in_the_prompt_toolkit_rules():
    from phil.ui.theme import prompt_toolkit_styles

    rules = prompt_toolkit_styles()
    for name in ("phil.muted", "phil.warn", "phil.error", "phil.gate.pass", "phil.id", "phil.cost"):
        assert name in rules


RUN2 = RunView(run_id="r-1", keyword="calc", node="implement", tasks_done=0, tasks_total=2, started=0.0)
MAIN = LiveStep(task="CALC-002", role="implementer", summary="run pytest -q", started=98.0)


def lines(view, now=100.0, width=None):
    return toolbar_text(render_live_rows(view, now, width)).split("\n") if render_live_rows(view, now, width) else []


def test_one_agent_is_exactly_todays_row():
    out = lines(ToolbarView(run=RUN2, live=MAIN))
    assert len(out) == 1 and out[0][2:] == "CALC-002 · implementer · run pytest -q · 2s"


def test_main_plus_a_sub_agent():
    view = ToolbarView(run=RUN2, live=MAIN, subs=(SubAgent(7, "explore tests", "read t.py", 94.0),))
    out = lines(view)
    assert out[1][2:] == " └ sub-agent · read t.py · explore tests · 6s"


def test_a_sub_agent_between_calls_says_working():
    view = ToolbarView(run=RUN2, live=MAIN, subs=(SubAgent(7, "explore tests", None, 94.0),))
    assert lines(view)[1][2:] == " └ sub-agent · working · explore tests · 6s"


def test_a_btw_line():
    view = ToolbarView(run=RUN2, live=MAIN, side=(SideJob('"why 3 tries?"', 96.0),))
    assert lines(view)[1][2:] == '/btw · "why 3 tries?" · 4s'


def test_more_than_three_collapses_and_nothing_wraps():
    subs = tuple(SubAgent(i, f"job {i}", None, 90.0) for i in range(4))
    view = ToolbarView(run=RUN2, live=MAIN, subs=subs, side=(SideJob("q", 99.0),))
    out = lines(view, width=40)
    assert len(out) == 4 and out[-1].strip() == "+3 more"
    assert all(cell_len(line) <= 39 for line in out)


def test_goal_step_without_a_run():
    view = ToolbarView(step="architect", step_started=88.0)
    assert lines(view)[0][2:] == "Architect drafting · 12s"


def test_feed_tag_ends_the_first_line():
    view = ToolbarView(run=RUN2, live=MAIN, feed_filter="tester")
    assert lines(view)[0].endswith("[feed: tester]")


def test_spinner_styles():
    view = ToolbarView(run=RUN2, live=MAIN, subs=(SubAgent(7, "x", None, 94.0),), side=(SideJob("q", 96.0),))
    frags = render_live_rows(view, 100.0)
    spinner_styles = [s for s, t in frags if t and t[0] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"]
    assert spinner_styles == ["class:phil.agent", "class:phil.sub", "class:phil.warn"]


def test_nothing_running_is_empty():
    assert render_live_rows(ToolbarView(), 0.0) == []
