from phil.chat.state import RunView, ToolbarView
from phil.ui.toolbar import SPINNER, render_toolbar


def test_idle():
    assert render_toolbar(ToolbarView(), now=0.0) == "Phil · type a goal, or /help"


def test_step_spinner_and_elapsed():
    view = ToolbarView(stage="planning", step="architect", step_started=100.0)
    assert render_toolbar(view, now=112.0) == f"{SPINNER[int(12 * 8) % len(SPINNER)]} Architect drafting · 12s"
    assert render_toolbar(view, now=100.0).startswith(SPINNER[0])


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
