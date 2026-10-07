from phil.chat.state import LiveStep, RunView, ToolbarView
from phil.ui.toolbar import SPINNER, render_live_row, render_toolbar


def test_idle():
    assert render_toolbar(ToolbarView(), now=0.0) == "Phil · type a goal, or /help"


def test_step_spinner_and_elapsed():
    view = ToolbarView(stage="planning", step="architect", step_started=100.0)
    assert render_toolbar(view, now=112.0) == f"{SPINNER[int(12 * 8) % len(SPINNER)]} Architect drafting · 12s"
    assert render_toolbar(view, now=100.0).startswith(SPINNER[0])


def test_designing_step_label():
    view = ToolbarView(stage="designing", step="designing", step_started=0.0)
    assert "Proposing approaches" in render_toolbar(view, now=1.0)


def test_run_pause_btw_and_cancelling():
    run = RunView("r-7f3a", "CALC", "implement", 1, 2, started=0.0)
    view = ToolbarView(stage="paused", run=run, paused=True, btw_pending=2)
    text = render_toolbar(view, now=185.0)
    assert "r-7f3a · CALC 1/2 · implement · 3m 05s" in text
    assert "⏸ r-7f3a needs you (/answer)" in text
    assert "/btw ×2" in text
    cancelling = ToolbarView(stage="planning", step="critic", step_started=0.0, cancelling=True)
    assert render_toolbar(cancelling, now=1.0).endswith("Critic reviewing · 1s (cancelling…)")


def test_long_elapsed():
    run = RunView("r-1", "X", None, 0, 1, started=0.0)
    assert "starting · 1h 01m" in render_toolbar(ToolbarView(stage="running", run=run), now=3660.0)


def test_width_drops_lower_priority_segments_then_truncates():
    from rich.cells import cell_len

    run = RunView("r-7f3a", "CALC", "implement", 1, 2, started=0.0)
    view = ToolbarView(stage="paused", step="critic", step_started=0.0, run=run, paused=True, btw_pending=3)
    full = render_toolbar(view, now=5.0)
    assert "Critic reviewing" in full and "/btw ×3" in full
    assert render_toolbar(view, now=5.0, width=200) == full
    narrow = render_toolbar(view, now=5.0, width=40)
    assert cell_len(narrow) < 40
    assert "needs you" in narrow  # the pause flag outlives the other segments
    tiny = render_toolbar(view, now=5.0, width=12)
    assert cell_len(tiny) < 12 and tiny.endswith("…")


def test_cost_segment_is_shown_and_dropped_first():
    from rich.cells import cell_len

    assert render_toolbar(ToolbarView(cost=(0.42, "reported")), now=0.0) == "Phil · type a goal, or /help  │  $0.42"
    run = RunView("r-7f3a", "CALC", "implement", 1, 2, started=0.0)
    view = ToolbarView(stage="running", run=run, btw_pending=1, cost=(1.5, "estimated"))
    full = render_toolbar(view, now=5.0)
    assert full.endswith("/btw ×1  │  ~$1.50")
    width = cell_len(full)  # one cell short: the cost goes first, /btw stays
    narrow = render_toolbar(view, now=5.0, width=width)
    assert "~$1.50" not in narrow and "/btw ×1" in narrow


def test_parked_count_is_shown_and_dropped_before_the_cost():
    from rich.cells import cell_len

    assert render_toolbar(ToolbarView(parked=3), now=0.0) == "Phil · type a goal, or /help  │  3 parked"
    view = ToolbarView(parked=2, cost=(0.5, "reported"))
    full = render_toolbar(view, now=0.0)
    assert full == "Phil · type a goal, or /help  │  $0.50  │  2 parked"
    narrow = render_toolbar(view, now=0.0, width=cell_len(full))
    assert "parked" not in narrow and "$0.50" in narrow


def test_live_row_shows_the_running_tool():
    run = RunView(run_id="r-1", keyword="calc", node="implement", tasks_done=0, tasks_total=2, started=0.0)
    view = ToolbarView(run=run, live=LiveStep(task="CALC-002", role="reviewer", summary="read README.md", started=100.0))
    assert render_live_row(view, now=108.0)[2:] == "CALC-002 · reviewer · read README.md · 8s"


def test_live_row_falls_back_to_the_stage_and_is_empty_without_a_run():
    run = RunView(run_id="r-1", keyword="calc", node="pick_task", tasks_done=0, tasks_total=2, started=0.0)
    assert render_live_row(ToolbarView(run=run), now=5.0)[2:] == "Picking the next task"
    assert render_live_row(ToolbarView(), now=5.0) == ""


def test_live_row_fits_the_width():
    run = RunView(run_id="r-1", keyword="calc", node="implement", tasks_done=0, tasks_total=2, started=0.0)
    view = ToolbarView(run=run, live=LiveStep(task="T1", role="implementer", summary="run " + "x" * 300, started=0.0))
    assert len(render_live_row(view, now=1.0, width=40)) <= 39
