from dataclasses import replace

from rich.cells import cell_len

from phil.chat.state import LiveStep, RunView, SideJob, SubAgent, ToolbarView
from phil.ui.toolbar import budget_style, render_live_rows, render_toolbar, short_model, toolbar_text

RUN = RunView(run_id="r-4f2a", keyword="calc", node="implement", tasks_done=1, tasks_total=3, started=0.0,
              name="calc-subtract", title="Add a subtract function")
FULL = ToolbarView(repo="calc", branch="phil/r-4f2a", run=RUN, model=("low", "gemini-3.8-flash"),
                   tokens=182_000, run_cost=(0.41, "reported"), budget_usd=1.0)


def text(view, now=64.0, width=None):
    return toolbar_text(render_toolbar(view, now, width))


def test_short_model():
    assert short_model("openrouter:google/gemini-3.8-flash") == "gemini-3.8-flash"
    assert short_model("ollama:qwen3:8b") == "qwen3:8b"
    assert short_model("model-x") == "model-x"
    assert short_model("anthropic:claude-sonnet-5") == "claude-sonnet-5"


def test_budget_style_thresholds():
    assert budget_style(0.79, 1.0) == "class:phil.cost"
    assert budget_style(0.80, 1.0) == "class:phil.warn"
    assert budget_style(1.00, 1.0) == "class:phil.error"
    assert budget_style(5.0, 0.0) == "class:phil.cost"


def test_run_in_progress_full_width():
    assert text(FULL) == ("calc @ phil/r-4f2a │ calc-subtract · task 2 of 3 · Add a subtract function │ 1m 04s │ "
                          "$0.41 / $1.00")


def test_a_run_shows_no_dots_model_tokens_or_node():
    out = text(FULL)
    assert not any(gone in out for gone in ("●", "◉", "gemini", "tok", "implement"))


def test_an_unnamed_run_shows_its_id():
    assert "calc @ phil/r-4f2a │ r-4f2a · task 2 of 3 · " in text(replace(FULL, run=replace(RUN, name=None)))


def test_planning_no_title_and_every_task_done():
    planning = replace(FULL, run=replace(RUN, tasks_done=0, tasks_total=0, title=None))
    assert "│ calc-subtract · planning │" in text(planning)
    assert "│ calc-subtract · task 2 of 3 │" in text(replace(FULL, run=replace(RUN, title=None)))
    assert "│ calc-subtract · Reviewing │" in text(replace(FULL, run=replace(RUN, tasks_done=3, node="review")))


def test_idle():
    view = ToolbarView(repo="calc", branch="main", cost=(0.06, "reported"))
    assert text(view) == "calc @ main │ Phil · type a goal, or /help │ chat $0.06"


def test_working_on_a_goal_leaves_the_step_to_the_live_row():
    """The step (`⠋ Architect drafting · 12s`) shows once, in the live row above the prompt."""
    view = ToolbarView(repo="calc", branch="main", step="architect", step_started=52.0,
                       model=("high", "claude-sonnet-5"), cost=(0.06, "reported"))
    assert text(view) == "calc @ main │ high·claude-sonnet-5 │ chat $0.06"
    assert lines(view, now=64.0)[0][2:] == "Architect drafting · 12s"


def test_a_cancelling_step_says_so_in_its_live_row():
    view = ToolbarView(step="architect", step_started=88.0, cancelling=True)
    assert lines(view)[0][2:] == "Architect drafting · 12s (cancelling…)"
    assert "cancelling" not in text(view)


def test_paused_shows_only_the_place_and_the_notice():
    paused = replace(FULL, paused=True)
    assert text(paused) == "calc @ phil/r-4f2a │ ⏸ calc-subtract needs you"
    assert text(replace(paused, btw_pending=1, parked=2)).endswith("│ ⏸ calc-subtract needs you │ /btw ×1 │ 2 parked")


def test_btw_and_parked_are_appended():
    view = ToolbarView(repo="calc", branch="main", btw_pending=2, parked=3)
    assert text(view).endswith("│ /btw ×2 │ 3 parked")


def test_no_budget_shows_cost_alone():
    view = ToolbarView(repo="calc", branch="b", run=RUN, run_cost=(0.41, "estimated"), budget_usd=0.0)
    assert text(view).endswith("│ ~$0.41")


def test_the_title_shortens_before_anything_drops():
    assert text(FULL, width=90) == ("calc @ phil/r-4f2a │ calc-subtract · task 2 of 3 · Add a subtra… │ 1m 04s │ "
                                    "$0.41 / $1.00")


def test_drop_order():
    # The title first shortens to 12 cells; then repo → elapsed → cost drop; the run segment is last.
    assert text(FULL, width=80) == "calc-subtract · task 2 of 3 · Add a subtr… │ 1m 04s │ $0.41 / $1.00"
    assert text(FULL, width=60) == "calc-subtract · task 2 of 3 · Add a subtr… │ $0.41 / $1.00"
    assert text(FULL, width=50) == "calc-subtract · task 2 of 3 · Add a subtr…"
    assert text(FULL, width=40).startswith("calc-subtract · task 2 of 3")


def test_extras_drop_first():
    view = replace(FULL, btw_pending=2, parked=3)
    out = text(view, width=90)  # the title is at its floor, then /btw and parked go, before the repo
    assert "parked" not in out and "/btw" not in out and out.startswith("calc @ phil/r-4f2a │ ")


def test_never_wraps_and_pause_survives():
    paused = replace(FULL, paused=True)
    for width in (20, 30, 40, 60, 80, 120):
        assert cell_len(text(FULL, width=width)) <= width - 1
        assert "⏸ calc-subtract needs you" in text(paused, width=max(width, 27))


def test_a_wide_title_and_a_long_name_never_overflow():
    run = replace(RUN, name="internationalisation-settings", title="日本語のタイトルを追加する" * 3)
    for paused in (False, True):
        view = replace(FULL, run=run, paused=paused, btw_pending=1, parked=4)
        for width in range(1, 121):
            assert cell_len(text(view, width=width)) <= max(width - 1, 0)


def test_full_line_fits_at_120():
    """At width 120 the full run line drops nothing."""
    assert text(FULL, width=120) == text(FULL)


def test_a_cut_pause_notice_keeps_its_warning_style():
    """When only the pause notice is left and it still has to be cut, it's cut with … and stays phil.warn."""
    paused = ToolbarView(repo="calc", branch="b", run=RUN, paused=True, run_cost=(0.41, "reported"), budget_usd=1.0)
    frags = render_toolbar(paused, 64.0, 12)
    assert toolbar_text(frags).endswith("…") and cell_len(toolbar_text(frags)) <= 11
    assert toolbar_text(frags).startswith("⏸ calc-sub")
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
    assert "calc-subtract" not in out
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


def test_the_main_row_names_the_agent_not_the_task():
    out = lines(ToolbarView(run=RUN2, live=MAIN))
    assert len(out) == 1 and out[0][2:] == "implementer · run pytest -q · 2s"


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


def test_more_than_six_collapses_and_nothing_wraps():
    subs = tuple(SubAgent(i, f"job {i}", None, 90.0) for i in range(6))
    view = ToolbarView(run=RUN2, live=MAIN, subs=subs, side=(SideJob("q", 99.0),))
    out = lines(view, width=40)
    assert len(out) == 6 and out[-1].strip() == "+3 more"
    assert all(cell_len(line) <= 39 for line in out)


ROLL = ("    edit   a.py", "    edit   b.py", "    … 3 more", "    run    make  → ok")


def test_the_rolling_lines_sit_under_the_main_row():
    out = lines(ToolbarView(run=RUN2, live=MAIN, rolling=ROLL))
    assert out[1:] == list(ROLL)


def test_the_live_area_is_capped_at_six_lines_rolling_lines_first():
    subs = (SubAgent(1, "x", None, 90.0), SubAgent(2, "y", None, 90.0))
    out = lines(ToolbarView(run=RUN2, live=MAIN, rolling=ROLL, subs=subs))
    assert len(out) == 6 and out[1:5] == list(ROLL) and out[5].strip() == "+2 more"


def test_under_a_callout_the_rolling_lines_collapse_without_counting_as_agents():
    text = toolbar_text(render_live_rows(ToolbarView(run=RUN2, live=MAIN, rolling=ROLL), 100.0, max_lines=1))
    assert "\n" not in text and "more" not in text


def test_a_multi_line_summary_keeps_the_live_area_within_its_lines():
    from phil.ui.bursts import Burst, rolling_lines

    heredoc = "run cat <<EOF > a.txt\nline one\n\x1b[31mred\x1b[0m\nEOF"
    calls = [{"seq": i, "phase": "end", "task": "T", "role": "implementer", "tool": "run_shell", "summary": heredoc,
              "result": "→ ok", "ok": True, "detail": None, "duration_ms": 0} for i in range(1, 8)]
    live = LiveStep(task="T", role="implementer", summary=heredoc, started=98.0)
    subs = (SubAgent(1, "a\nb", "run x\ny", 90.0), SubAgent(2, "y", None, 90.0))
    view = ToolbarView(run=RUN2, live=live, rolling=rolling_lines(Burst("T", "implementer", calls)), subs=subs)
    rendered = toolbar_text(render_live_rows(view, 100.0))
    assert "\x1b" not in rendered and len(rendered.split("\n")) <= 6


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


def test_a_run_with_no_live_step_shows_the_stage_label():
    """The old behaviour: a run with no running tool (`live is None`) falls back to its node's
    stage label, not an empty row."""
    out = lines(ToolbarView(run=RUN))
    assert out[0][2:] == "Implementing"


def test_feed_tag_never_overflows_at_any_width():
    """Even when the `[feed: x]` tag alone wouldn't fit in `width - 1`, the whole row (tag
    included) is cut, so no line this returns ever exceeds `width - 1` cells."""
    subs = tuple(SubAgent(i, f"job {i}", None, 90.0) for i in range(2))
    view = ToolbarView(run=RUN2, live=MAIN, subs=subs, side=(SideJob("日本語テスト", 99.0),),
                       feed_filter="implementer")
    for width in range(1, 121):
        out = lines(view, width=width)
        assert all(cell_len(line) <= width - 1 for line in out)


def test_max_lines_one_collapses_to_a_single_line_with_a_more_count():
    """`max_lines=1` is the decision callout's budget: everything collapses to the first row,
    with ` +N more` for the agents that no longer get their own line."""
    subs = (SubAgent(1, "a", None, 90.0), SubAgent(2, "b", None, 90.0))
    view = ToolbarView(run=RUN2, live=MAIN, subs=subs)
    frags = render_live_rows(view, 100.0, max_lines=1)
    text = toolbar_text(frags)
    assert "\n" not in text
    assert text.endswith(" +2 more")


def test_max_lines_one_with_a_single_agent_has_no_more_suffix():
    view = ToolbarView(run=RUN2, live=MAIN)
    frags = render_live_rows(view, 100.0, max_lines=1)
    assert "more" not in toolbar_text(frags)


LONG_MAIN = LiveStep(task="CALC-002", role="implementer", summary="run " + "pytest -q tests/" * 10, started=98.0)


def test_a_cut_collapsed_row_keeps_its_more_count():
    subs = (SubAgent(1, "a", None, 90.0), SubAgent(2, "b", None, 90.0))
    view = ToolbarView(run=RUN2, live=LONG_MAIN, subs=subs)
    text = toolbar_text(render_live_rows(view, 100.0, 80, max_lines=1))
    assert text.endswith("+2 more")
    assert cell_len(text) <= 79 and "…" in text


def test_a_cut_row_keeps_both_suffixes():
    subs = (SubAgent(1, "a", None, 90.0), SubAgent(2, "b", None, 90.0))
    view = ToolbarView(run=RUN2, live=LONG_MAIN, subs=subs, feed_filter="tester")
    text = toolbar_text(render_live_rows(view, 100.0, 60, max_lines=1))
    assert text.endswith(" +2 more   [feed: tester]")
    assert cell_len(text) <= 59 and "…" in text


def test_a_long_sub_agent_line_keeps_its_time():
    sub = SubAgent(7, "explore the whole test suite for flaky timing " * 3, "read " + "deep/" * 20 + "t.py", 94.0)
    view = ToolbarView(run=RUN2, live=MAIN, subs=(sub,))
    for width in (80, 40):
        line = lines(view, width=width)[1]
        assert line.endswith(" · 6s"), line
        assert cell_len(line) <= width - 1
        assert "└ sub-agent · " in line
    line = lines(view, width=80)[1]
    summary, description = line.split(" · ")[1:3]
    assert summary.startswith("read deep/") and description.endswith("…") and cell_len(description) >= 8


def test_a_cut_row_keeps_each_fragments_style():
    subs = (SubAgent(1, "a", None, 90.0), SubAgent(2, "b", None, 90.0))
    view = ToolbarView(run=RUN2, live=LONG_MAIN, subs=subs, feed_filter="tester")
    frags = render_live_rows(view, 100.0, 60, max_lines=1)
    assert frags[0][0] == "class:phil.agent" and frags[0][1] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    assert ("class:phil.muted", " +2 more") in frags
    assert ("class:phil.muted", "   [feed: tester]") in frags
    body = [f for f in frags if "implementer" in f[1]]
    assert len(body) == 1 and body[0][0] == "" and body[0][1].endswith("…")
