"""`ScreenIO` as the controller's `TaskOutput`: a task's lines grouped in a `TaskBlock` that folds to
one line when the task is done."""

from rich.text import Text

from phil.ui.screen.io import ScreenIO
from phil.ui.screen.widgets import Conversation, Item, NewMarker, TaskBlock
from tests.ui.screen.pilot import pilot_run, render, until

BAND = Text("▶ Task 1 · Theme setting")
FOLDED = Text("✓ Task 1 · Theme setting · done · 2 files · 38s")


def _blocks(io) -> list[TaskBlock]:
    return list(io.app.query_one(Conversation).query(TaskBlock))


def test_screen_io_is_the_chat_task_output():
    io = ScreenIO()
    assert io.chat_io(lambda *args: None).task_output is io


def test_task_lines_group_under_their_block():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("t1", BAND)
        io.task_line("t1", Text("  edit settings.tsx"))
        io.task_line("t1", Text("  run tests"))
        await until(pilot, lambda: len(_blocks(io)) == 1 and len(_blocks(io)[0].lines) == 2)
        await pilot.pause()
        block = _blocks(io)[0]
        assert [item.renderable.plain for item in block.lines] == ["  edit settings.tsx", "  run tests"]
        assert all(isinstance(item, Item) for item in block.lines)   # selectable, as items are
        assert render(block).splitlines()[0].startswith("▶ Task 1 · Theme setting")
        assert all(item.parent is block for item in io.app.query_one(Conversation).query(Item))

    pilot_run(io.app, script)


def test_task_done_folds_to_one_line():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("t1", BAND)
        io.task_line("t1", Text("  edit settings.tsx"))
        io.task_line("t1", Text("  run tests"))
        io.task_done("t1", FOLDED)
        await until(pilot, lambda: _blocks(io) and _blocks(io)[0].folded)
        await pilot.pause()
        block = _blocks(io)[0]
        assert block.lines == []
        assert render(block).strip() == "✓ Task 1 · Theme setting · done · 2 files · 38s"
        assert block.outer_size.height == 1

    pilot_run(io.app, script)


def test_a_line_after_the_fold_opens_the_block_again():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("t1", BAND)
        io.task_line("t1", Text("  first try"))
        io.task_done("t1", FOLDED)
        io.task_line("t1", Text("  retry"))
        await until(pilot, lambda: _blocks(io) and len(_blocks(io)[0].lines) == 1)
        block = _blocks(io)[0]
        assert not block.folded
        assert block.header.renderable.plain == "▶ Task 1 · Theme setting"
        assert block.lines[0].renderable.plain == "  retry"

    pilot_run(io.app, script)


def test_an_unannounced_task_gets_a_block_headed_by_its_id():
    io = ScreenIO()

    async def script(pilot):
        io.task_line("t7", Text("  a line"))
        io.task_done("t8", FOLDED)
        await until(pilot, lambda: len(_blocks(io)) == 2)
        first, second = _blocks(io)
        assert first.header.renderable.plain == "t7" and first.lines[0].renderable.plain == "  a line"
        assert second.folded and second.header.renderable.plain == FOLDED.plain

    pilot_run(io.app, script)


def test_sub_agent_line_shows_once():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("t1", BAND)
        io.sub_agent("t1", 1, "find theme")
        io.sub_agent("t1", 1, "find theme")
        io.sub_agent("t1", 2, "check tests")
        await until(pilot, lambda: _blocks(io) and len(_blocks(io)[0].lines) == 2)
        await pilot.pause()
        lines = _blocks(io)[0].lines
        assert [item.renderable.plain for item in lines] == ["└ sub-agent  find theme", "└ sub-agent  check tests"]
        assert lines[0].renderable.style == "phil.sub"

    pilot_run(io.app, script)


def test_two_tasks_two_blocks_in_order():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("t1", Text("▶ Task 1"))
        io.task_started("t2", Text("▶ Task 2"))
        io.task_line("t1", Text("  one"))
        io.task_line("t2", Text("  two"))
        await until(pilot, lambda: len(_blocks(io)) == 2 and all(b.lines for b in _blocks(io)))
        first, second = _blocks(io)
        assert first.header.renderable.plain == "▶ Task 1" and first.lines[0].renderable.plain == "  one"
        assert second.header.renderable.plain == "▶ Task 2" and second.lines[0].renderable.plain == "  two"

    pilot_run(io.app, script)


def test_block_lines_follow_at_the_bottom_and_mark_when_scrolled_up():
    io = ScreenIO()

    async def script(pilot):
        conversation = io.app.query_one(Conversation)
        marker = io.app.query_one(NewMarker)
        io.task_started("t1", BAND)
        for n in range(60):
            io.task_line("t1", Text(f"  line {n}"))
        await until(pilot, lambda: conversation.max_scroll_y > 0 and conversation.scroll_y == conversation.max_scroll_y)
        io.task_line("t1", Text("  at the bottom"))
        await until(pilot, lambda: len(_blocks(io)[0].lines) == 61)
        await until(pilot, lambda: conversation.scroll_y == conversation.max_scroll_y)
        assert not marker.display
        conversation.scroll_home(animate=False)
        await until(pilot, lambda: conversation.scroll_y == 0)
        io.task_line("t1", Text("  while scrolled up"))
        await until(pilot, lambda: marker.display)
        await pilot.pause()
        assert conversation.scroll_y == 0

    pilot_run(io.app, script)
