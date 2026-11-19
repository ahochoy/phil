"""The frame's live line (`LiveLine`): the live rows on the left, time and cost on the right of the
first row, refreshed every 0.5 s from the view `ScreenIO.set_view` gives; hidden when idle."""

import logging
import time
from dataclasses import replace

from rich.cells import cell_len
from rich.text import Text

from phil.chat.decision import Decision, Option
from phil.chat.state import LiveStep, RunView, SubAgent, ToolbarView
from phil.ui.screen.io import ScreenIO
from phil.ui.screen.widgets import Conversation, DecisionMenu, Item, LiveLine
from tests.ui.screen.pilot import pilot_run, render, until

D = Decision("pause", "⏸ r-1 needs you · setup failed", ("x",),
             (Option("Retry the task", "retry"), Option("Abort the run", "abort")))


def _view(**changes) -> ToolbarView:
    now = time.time()
    run = RunView(run_id="r1", keyword="", node="implement", tasks_done=1, tasks_total=3, started=now - 72,
                  name="dark-mode", title="Toggle")
    view = ToolbarView(run=run, live=LiveStep(task=None, role="implementer", summary="edit settings.tsx",
                                              started=now - 1),
                       run_cost=(0.06, "reported"))
    return replace(view, **changes)


def _live(io) -> LiveLine:
    return io.app.query_one(LiveLine)


def _rows(io) -> list[str]:
    return render(_live(io)).splitlines()


def test_live_line_hidden_when_idle():
    io = ScreenIO()
    io.set_view(lambda: ToolbarView())

    async def script(pilot):
        await pilot.pause(0.7)                     # past a refresh
        assert not _live(io).display

    pilot_run(io.app, script)


def test_live_line_hidden_without_a_view():
    io = ScreenIO()

    async def script(pilot):
        await pilot.pause(0.7)
        assert not _live(io).display

    pilot_run(io.app, script)


def test_live_line_shows_agent_task_count_and_cost():
    io = ScreenIO()
    io.set_view(_view)

    async def script(pilot):
        live = _live(io)
        await until(pilot, lambda: live.display and live.outer_size.height == 1)
        await pilot.pause()
        row = _rows(io)[0]
        left, right = row.rsplit("  ", 1)
        assert "implementer" in left and left.rstrip().endswith("· task 2/3")
        assert right.endswith("$0.06")
        assert cell_len(row) == live.outer_size.width - 1       # right-aligned, inside the padding

    pilot_run(io.app, script)


def test_live_line_grows_with_sub_agents_and_shrinks_back():
    io = ScreenIO()
    state = {"view": _view()}
    io.set_view(lambda: state["view"])

    async def script(pilot):
        live = _live(io)
        await until(pilot, lambda: live.display and live.outer_size.height == 1)
        now = time.time()
        state["view"] = _view(subs=(SubAgent(1, "find theme", "grep x", now), SubAgent(2, "check tests", None, now)))
        await until(pilot, lambda: live.outer_size.height == 3)
        rows = _rows(io)
        assert "└ sub-agent" in rows[1] and "find theme" in rows[1] and "check tests" in rows[2]
        state["view"] = _view()
        await until(pilot, lambda: live.outer_size.height == 1)
        state["view"] = ToolbarView()
        await until(pilot, lambda: not live.display)

    pilot_run(io.app, script)


def test_a_view_that_raises_is_logged_and_the_next_tick_renders(caplog):
    io = ScreenIO()
    calls = []

    def view():
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("bad view")
        return _view()

    io.set_view(view)

    async def script(pilot):
        live = _live(io)
        await until(pilot, lambda: len(calls) >= 3 and live.display)
        await pilot.pause()
        assert io.app.is_running and "task 2/3" in _rows(io)[0]
        assert any("bad view" in str(r.exc_info) for r in caplog.records if r.name.startswith("phil.ui.screen"))

    with caplog.at_level(logging.ERROR):
        pilot_run(io.app, script)
    assert not io.app.return_code


def test_a_view_that_keeps_failing_is_logged_once_until_it_recovers(caplog):
    io = ScreenIO()
    state = {"fail": True}

    def view():
        if state["fail"]:
            raise ValueError("bad view")
        return _view()

    io.set_view(view)

    def logged() -> int:
        return sum("bad view" in str(r.exc_info) for r in caplog.records)

    async def script(pilot):
        live = _live(io)
        await pilot.pause()
        for _ in range(5):
            live.tick()
        assert logged() == 1                       # the first failure, not one per tick
        state["fail"] = False
        live.tick()
        state["fail"] = True
        live.tick()
        live.tick()
        assert logged() == 2                       # it recovered, then failed again

    with caplog.at_level(logging.ERROR):
        pilot_run(io.app, script)


def test_an_idle_tick_does_not_relayout():
    io = ScreenIO()
    io.set_view(_view)
    layouts = []

    async def script(pilot):
        live = _live(io)
        await until(pilot, lambda: live.display)
        original = live.refresh

        def refresh(*args, **kwargs):
            layouts.append(kwargs.get("layout", False))
            return original(*args, **kwargs)

        live.refresh = refresh  # type: ignore[method-assign]
        live.tick()
        live.tick()
        assert layouts and not any(layouts)

    pilot_run(io.app, script)


def test_live_line_keeps_to_one_row_while_a_menu_shows():
    io = ScreenIO()
    now = time.time()
    io.set_view(lambda: _view(subs=(SubAgent(1, "find theme", None, now), SubAgent(2, "check tests", None, now))))

    async def script(pilot):
        live = _live(io)
        await until(pilot, lambda: live.display and live.outer_size.height == 3)
        live.set_max_lines(1)
        await until(pilot, lambda: live.outer_size.height == 1)
        assert "+2 more" in _rows(io)[0]

    pilot_run(io.app, script)


def test_live_line_fits_a_narrow_pane():
    io = ScreenIO()
    io.set_view(lambda: _view(budget_usd=1.0, live=LiveStep(task=None, role="implementer",
                                                            summary="edit " + "x" * 200, started=time.time())))

    async def script(pilot):
        live = _live(io)
        await until(pilot, lambda: live.display)
        await pilot.resize_terminal(60, 20)
        await pilot.pause(0.7)
        await pilot.pause()
        assert all(cell_len(row) <= live.outer_size.width for row in _rows(io))
        assert _rows(io)[0].endswith("$0.06 / $1.00")

    pilot_run(io.app, script, size=(100, 20))


def test_resize_to_60_columns_rerenders_without_error(caplog):     # Review Focus 5
    io = ScreenIO()
    chat = io.chat_io(lambda *args: None)
    io.set_view(_view)
    got = []

    async def script(pilot):
        io.app.post_item(Text(" ".join(f"word{n}" for n in range(80))))
        io.task_started("t1", Text("▶ Task 1 · " + "long title " * 12))
        io.start(lambda: got.append(chat.choose("", D)))
        menu = io.app.query_one(DecisionMenu)
        await until(pilot, lambda: menu.display and io.app.focused is menu)
        await pilot.pause()
        await pilot.resize_terminal(60, 20)
        await pilot.pause(0.7)
        await pilot.pause()
        items = list(io.app.query_one(Conversation).query(Item))
        assert len(items) == 2
        assert all(cell_len(line) <= 60 for item in items for line in render(item).splitlines())
        assert all(cell_len(line) <= 60 for line in render(menu).splitlines())
        assert menu.display and render(menu).strip()
        await pilot.press("enter")
        await until(pilot, lambda: got == ["retry"])

    with caplog.at_level(logging.ERROR):
        pilot_run(io.app, script)
    assert not io.app.return_code
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
