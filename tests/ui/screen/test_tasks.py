"""`ScreenIO` as the controller's `TaskOutput`: a task's lines grouped in a `TaskBlock` that folds to
one line when the task is done."""

from rich.text import Text

from phil.chat.events import ChatEvent
from phil.ui.screen.io import ScreenIO
from phil.ui.screen.widgets import Conversation, Item, NewMarker, TaskBlock
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
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
        # The controller announces each (task, seq) once per run (`_announced_subs`); the frame
        # shows each announcement as one line.
        io.task_started("t1", BAND)
        io.sub_agent("t1", 1, "find theme")
        io.sub_agent("t1", 2, "check tests")
        await until(pilot, lambda: _blocks(io) and len(_blocks(io)[0].lines) == 2)
        await pilot.pause()
        lines = _blocks(io)[0].lines
        assert [item.renderable.plain for item in lines] == ["└ sub-agent  find theme", "└ sub-agent  check tests"]
        assert lines[0].renderable.style == "phil.sub"

    pilot_run(io.app, script)


def test_a_sub_agent_description_cannot_reach_the_terminal_as_escape_codes():
    """The description is model output: ESC and newlines show as `?` and a space, on one line."""
    io = ScreenIO()

    async def script(pilot):
        io.task_started("t1", BAND)
        io.sub_agent("t1", 1, "evil\x1b]52;c;aGk=\x07\nnext")
        await until(pilot, lambda: _blocks(io) and len(_blocks(io)[0].lines) == 1)
        assert _blocks(io)[0].lines[0].renderable.plain == "└ sub-agent  evil?]52;c;aGk=? next"

    pilot_run(io.app, script)


def test_a_later_run_reusing_a_task_id_gets_a_new_block_with_its_sub_agents():
    io = ScreenIO()

    async def script(pilot):
        for run in ("first", "second"):
            io.task_started("Task 1", Text(f"▶ Task 1 · {run}"))
            io.sub_agent("Task 1", 1, f"{run} helper")
            io.task_done("Task 1", Text(f"✓ Task 1 · {run} · done"))
        io.task_line("Task 1", Text("  after both"))
        await until(pilot, lambda: len(_blocks(io)) == 2 and len(_blocks(io)[1].lines) == 1)
        await pilot.pause()
        first, second = _blocks(io)
        assert first.folded and first.header.renderable.plain == "✓ Task 1 · first · done"
        assert second.header.renderable.plain == "▶ Task 1 · second"      # opened again by the line
        assert [item.renderable.plain for item in second.lines] == ["  after both"]

    pilot_run(io.app, script)


def test_each_run_shows_its_sub_agent_line():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("Task 1", Text("▶ Task 1 · first"))
        io.sub_agent("Task 1", 1, "first helper")
        io.task_started("Task 1", Text("▶ Task 1 · second"))
        io.sub_agent("Task 1", 1, "second helper")
        await until(pilot, lambda: len(_blocks(io)) == 2 and all(b.lines for b in _blocks(io)))
        first, second = _blocks(io)
        assert [item.renderable.plain for item in first.lines] == ["└ sub-agent  first helper"]
        assert [item.renderable.plain for item in second.lines] == ["└ sub-agent  second helper"]

    pilot_run(io.app, script)


def test_a_line_after_clear_conversation_shows_in_a_new_block():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("Task 1", BAND)
        io.task_line("Task 1", Text("  before"))
        await until(pilot, lambda: _blocks(io) and _blocks(io)[0].lines)
        io.app.clear_conversation()
        io.task_line("Task 1", Text("  after"))
        await until(pilot, lambda: len(_blocks(io)) == 1 and _blocks(io)[0].header.renderable.plain == "Task 1")
        await pilot.pause()
        assert [item.renderable.plain for item in _blocks(io)[0].lines] == ["  after"]

    pilot_run(io.app, script)


def test_a_task_block_spaces_its_burst_and_folds_to_one_line(calc_repo):
    """Through the controller's task output: the burst summary starts a block, so one empty item
    comes before it inside the task block (and before the gate band after it), none before the
    engine's gate line; the fold leaves only the folded line."""
    box = {}

    def grab(controller):
        box["controller"] = controller
        return None

    run_chat(calc_repo, ["add subtract", "y", grab], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    controller = box["controller"]
    io = ScreenIO()
    controller.io.task_output = io
    end = {"ts": "2026-10-07T10:00:05Z", "phase": "end", "task": "CALC-001", "tool": "run_shell", "result": "→ ok",
           "ok": True}

    async def script(pilot):
        controller._handle(ChatEvent("milestone", {
            "kind": "task_started", "task": "CALC-001", "title": "Add", "ts": "2026-10-07T10:00:00Z", "seq": 2}))
        controller._handle(ChatEvent("activity", {"records": [
            {**end, "seq": 3, "role": "engine", "summary": "run pytest -q"},
            {**end, "seq": 4, "role": "implementer", "summary": "run make"},
        ]}))
        controller._handle(ChatEvent("milestone", {"kind": "gate", "task": "CALC-001", "name": "green", "seq": 5}))
        await until(pilot, lambda: _blocks(io) and len(_blocks(io)[0].lines) == 5)
        await pilot.pause()
        lines = [item.renderable.plain for item in _blocks(io)[0].lines]
        assert "pytest -q" in lines[0]
        assert lines[1] == "" and "implementer ran 1 tool · ran make" in lines[2]
        assert lines[3] == "" and lines[4].startswith("✓ CALC-001 green")
        assert all(item.outer_size.height == 1 for item in _blocks(io)[0].lines)  # an empty item is one row
        controller._handle(ChatEvent("milestone", {
            "kind": "task_done", "task": "CALC-001", "ts": "2026-10-07T10:00:38Z", "seq": 9}))
        await until(pilot, lambda: _blocks(io)[0].folded)
        await pilot.pause()
        block = _blocks(io)[0]
        assert block.lines == [] and block.outer_size.height == 1
        assert render(block).strip().startswith("✓ CALC-001")

    pilot_run(io.app, script)


def test_a_task_block_has_one_blank_row_above_it_unless_it_comes_first():
    io = ScreenIO()

    async def script(pilot):
        io.task_started("t1", Text("▶ Task 1"))
        io.app.post_item(Text("between"))
        io.task_started("t2", Text("▶ Task 2"))
        await until(pilot, lambda: len(_blocks(io)) == 2)
        await pilot.pause()
        conversation = io.app.query_one(Conversation)
        first, second = _blocks(io)
        [between] = [item for item in conversation.query(Item) if item.parent is conversation]
        assert first.region.y == conversation.content_region.y  # none at the very top
        assert between.region.y == first.region.y + 1  # a plain item has no margin of its own
        assert second.region.y == between.region.y + 2  # one blank row above a later task block

    pilot_run(io.app, script)


def test_an_echoed_line_has_one_blank_row_above_it_unless_it_comes_first():
    io = ScreenIO()

    async def script(pilot):
        io.app.post_echo(Text("you › first"))
        io.app.post_item(Text("reply"))
        io.app.post_echo(Text("you › second"))
        conversation = io.app.query_one(Conversation)
        await until(pilot, lambda: len(list(conversation.query(Item))) == 3)
        await pilot.pause()
        first, reply, second = conversation.query(Item)
        assert first.region.y == conversation.content_region.y
        assert reply.region.y == first.region.y + 1
        assert second.region.y == reply.region.y + 2

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
