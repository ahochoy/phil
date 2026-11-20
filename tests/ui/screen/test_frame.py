import threading

from rich.console import Console
from rich.text import Text

from phil.chat.controller import help_view
from phil.chat.decision import Decision, Option
from phil.ui.callout import callout_lines, to_rich
from phil.ui.screen.app import PhilApp
from phil.ui.screen.console import PaneConsole
from phil.ui.screen.themed import Themed
from phil.ui.screen.widgets import (
    Conversation,
    DecisionMenu,
    HeaderBar,
    Item,
    LiveLine,
    NewMarker,
    PromptBox,
)
from tests.ui.screen.pilot import drag, pilot_run, render, until


def test_themed_resolves_phil_styles():
    console = Console(record=True, color_system="truecolor", width=20)
    console.print(Themed(Text("x", style="phil.warn")))
    assert "\x1b[33m" in console.export_text(styles=True)   # yellow


def test_pane_console_posts_markup_as_text():
    posted = []
    PaneConsole(posted.append, lambda: 50).print("[phil.muted]hi[/]")
    assert posted[0].plain == "hi" and posted[0].spans[0].style == "phil.muted"


def test_pane_console_joins_objects_and_reads_width():
    posted = []
    width = [50]
    console = PaneConsole(posted.append, lambda: width[0])
    console.print("a", Text("b"), sep="-")
    assert posted[0].plain == "a-b"
    width[0] = 72
    assert console.width == 72


def test_pane_console_has_no_input():
    console = PaneConsole(lambda _: None, lambda: 50)
    try:
        console.input("? ")
    except RuntimeError:
        pass
    else:
        raise AssertionError("input should raise")


def test_header_and_items_show():
    app = PhilApp()
    app.set_header("my-site", "main", "c-20261118-120000")
    app.post_item(Text("hello"))

    async def script(pilot):
        await pilot.pause()
        assert "◆ phil · my-site · main" in render(app.query_one(HeaderBar))
        assert "chat c-20261118-120000" in render(app.query_one(HeaderBar))
        assert "hello" in render(app.query_one(Conversation))

    pilot_run(app, script)


def test_items_render_phil_styles():
    app = PhilApp()

    async def script(pilot):
        app.post_item(Text("careful", style="phil.warn"))
        app.post_item(Text("plain"))
        await pilot.pause()

        def colors(item):
            strip = item.render_lines(item.region.reset_offset)[0]
            return {segment.style.color for segment in strip if segment.text.strip()}

        warn, plain = (colors(item) for item in app.query_one(Conversation).query(Item))
        assert len(warn) == 1 and warn != plain       # phil.warn resolved, not the default colour

    pilot_run(app, script)


def test_hidden_widgets_start_hidden_and_prompt_has_focus():
    app = PhilApp()

    async def script(pilot):
        await pilot.pause()
        for kind in (NewMarker, LiveLine, DecisionMenu):
            assert not app.query_one(kind).display
        assert app.focused is app.query_one(PromptBox)

    pilot_run(app, script)


def test_prompt_grows_with_its_text_up_to_six_lines():
    app = PhilApp()

    async def script(pilot):
        prompt = app.query_one(PromptBox)
        await pilot.pause()
        assert prompt.outer_size.height == 3                    # one line inside the border
        prompt.text = "\n".join(f"line {n}" for n in range(3))
        await until(pilot, lambda: prompt.outer_size.height == 5)
        prompt.text = "\n".join(f"line {n}" for n in range(20))
        await pilot.pause()
        await until(pilot, lambda: prompt.outer_size.height == 8)  # six lines, then it scrolls

    pilot_run(app, script)


def test_follows_new_output_at_the_bottom():
    app = PhilApp()

    async def script(pilot):
        conversation = app.query_one(Conversation)
        for n in range(100):
            app.post_item(Text(f"line {n}"))
        await until(pilot, lambda: conversation.max_scroll_y > 0 and conversation.scroll_y == conversation.max_scroll_y)
        assert not app.query_one(NewMarker).display

    pilot_run(app, script)


def test_scrolled_up_new_output_shows_marker_and_holds_view():
    app = PhilApp()

    async def script(pilot):
        conversation = app.query_one(Conversation)
        marker = app.query_one(NewMarker)
        for n in range(100):
            app.post_item(Text(f"line {n}"))
        await until(pilot, lambda: conversation.max_scroll_y > 0 and conversation.scroll_y == conversation.max_scroll_y)
        conversation.scroll_home(animate=False)
        await until(pilot, lambda: conversation.scroll_y == 0)
        app.post_item(Text("more"))
        await until(pilot, lambda: marker.display)
        await pilot.pause()
        assert conversation.scroll_y == 0
        await pilot.press("x")
        await until(pilot, lambda: conversation.scroll_y == conversation.max_scroll_y)
        assert not marker.display
        assert app.query_one(PromptBox).text == "x"

    pilot_run(app, script)


def test_scrolling_to_the_bottom_hides_the_marker():
    app = PhilApp()

    async def script(pilot):
        conversation = app.query_one(Conversation)
        marker = app.query_one(NewMarker)
        for n in range(100):
            app.post_item(Text(f"line {n}"))
        await until(pilot, lambda: conversation.max_scroll_y > 0 and conversation.scroll_y == conversation.max_scroll_y)
        conversation.scroll_home(animate=False)
        await until(pilot, lambda: conversation.scroll_y == 0)
        app.post_item(Text("more"))
        await until(pilot, lambda: marker.display)
        conversation.scroll_end(animate=False)
        await until(pilot, lambda: not marker.display)
        app.post_item(Text("and more"))
        await until(pilot, lambda: conversation.scroll_y == conversation.max_scroll_y and conversation.max_scroll_y > 0)
        await pilot.pause()
        assert not marker.display

    pilot_run(app, script)


def test_scroll_keys_scroll_the_conversation_from_the_prompt():
    app = PhilApp()

    async def script(pilot):
        conversation = app.query_one(Conversation)
        for n in range(100):
            app.post_item(Text(f"line {n}"))
        await until(pilot, lambda: conversation.max_scroll_y > 0 and conversation.scroll_y == conversation.max_scroll_y)
        bottom = conversation.scroll_y
        await pilot.press("pageup")
        await until(pilot, lambda: conversation.scroll_y < bottom)
        paged = conversation.scroll_y
        await pilot.press("shift+up")
        await until(pilot, lambda: conversation.scroll_y < paged)
        lined = conversation.scroll_y
        await pilot.press("shift+down")
        await until(pilot, lambda: conversation.scroll_y > lined)
        await pilot.press("pagedown")
        await until(pilot, lambda: conversation.scroll_y == conversation.max_scroll_y)
        assert app.focused is app.query_one(PromptBox) and app.query_one(PromptBox).text == ""

    pilot_run(app, script)


def test_clear_conversation_empties_it():
    app = PhilApp()

    async def script(pilot):
        app.post_item(Text("hello"))
        await pilot.pause()
        app.clear_conversation()
        await until(pilot, lambda: not app.query_one(Conversation).children)

    pilot_run(app, script)


def test_post_from_another_thread_shows():
    app = PhilApp()

    async def script(pilot):
        await pilot.pause()
        worker = threading.Thread(target=app.post_item, args=(Text("from a thread"),))
        worker.start()
        worker.join()
        await until(pilot, lambda: "from a thread" in render(app.query_one(Conversation)))

    pilot_run(app, script)


def test_no_quit_binding_or_command_palette():
    app = PhilApp()
    assert PhilApp.ENABLE_COMMAND_PALETTE is False
    assert PhilApp.ALLOW_SELECT is True

    async def script(pilot):
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app.is_running
        assert all(binding.key != "ctrl+q" for (_, binding, *_rest) in app.active_bindings.values())

    pilot_run(app, script)


def test_post_after_exit_is_dropped():          # Review Focus 2
    app = PhilApp()

    async def leave(pilot):
        pilot.app.exit()

    pilot_run(app, leave)
    app.post_item(Text("late"))                  # must not raise
    app.set_header("r", "b", "c")
    app.clear_conversation()


CALLOUT = Decision("approval", "⏸ r-1 needs you · approve a command", ("CALC-002 wants to run:", "  node build.mjs"),
                   (Option("Approve for this run", "approve"), Option("Deny", "deny")))


def _items(app):
    return list(app.query_one(Conversation).query(Item))


def test_drag_selects_a_text_item():
    app = PhilApp()

    async def script(pilot):
        app.post_item(Text("hello selectable world", style="phil.warn"))
        await until(pilot, lambda: len(_items(app)) == 1)
        await pilot.pause()
        await drag(pilot, _items(app)[0], (0, 0), (15, 0))
        assert "hello selectable" in app.screen.get_selected_text()

    pilot_run(app, script)


def test_drag_selects_a_callout_item():
    app = PhilApp()

    async def script(pilot):
        app.post_item(to_rich(callout_lines(CALLOUT, 0, 80, live=False)))
        await until(pilot, lambda: len(_items(app)) == 1)
        await pilot.pause()
        item = _items(app)[0]
        await drag(pilot, item, (0, 0), (item.size.width - 1, item.size.height - 1))
        selected = app.screen.get_selected_text()
        assert "CALC-002 wants to run:" in selected and "Approve for this run" in selected

    pilot_run(app, script)


def test_drag_selects_a_group_item():
    app = PhilApp()

    async def script(pilot):
        app.post_item(help_view())
        await until(pilot, lambda: len(_items(app)) == 1)
        await pilot.pause()
        item = _items(app)[0]
        await drag(pilot, item, (0, 0), (item.size.width - 1, min(item.size.height, 20) - 1))
        assert "/" in app.screen.get_selected_text()    # the slash commands are selectable text

    pilot_run(app, script)


def test_items_reflow_on_resize():
    app = PhilApp()

    async def script(pilot):
        app.post_item(Text(" ".join(f"word{n}" for n in range(40))))
        await until(pilot, lambda: len(_items(app)) == 1)
        await pilot.pause()
        item = _items(app)[0]
        wide = item.size.height
        await pilot.resize_terminal(60, 30)
        await until(pilot, lambda: item.size.height > wide)
        assert all(len(line) <= 60 for line in render(item).splitlines())

    pilot_run(app, script)


def test_a_failing_cross_thread_call_is_logged_not_printed(capfd, caplog):
    app = PhilApp()

    def broken():
        raise ValueError("boom")

    async def script(pilot):
        await pilot.pause()
        worker = threading.Thread(target=app._call, args=(broken,))
        worker.start()
        worker.join()
        await until(pilot, lambda: any("boom" in str(r.exc_info) for r in caplog.records))
        assert all(r.name.startswith("phil.") for r in caplog.records if "boom" in str(r.exc_info))
        app.post_item(Text("still alive"))
        await until(pilot, lambda: "still alive" in render(app.query_one(Conversation)))
        assert app.is_running

    pilot_run(app, script)
    assert "boom" not in capfd.readouterr().err


def test_cross_thread_calls_run_in_app_context():
    from textual._context import active_app, active_message_pump

    app = PhilApp()
    seen = []

    async def script(pilot):
        await pilot.pause()
        worker = threading.Thread(target=app._call, args=(lambda: seen.append((active_app.get(), active_message_pump.get())),))
        worker.start()
        worker.join()
        await until(pilot, lambda: seen)
        assert seen[0][0] is app and seen[0][1] is app

    pilot_run(app, script)


def test_items_appear_in_call_order_across_threads():
    app = PhilApp()

    async def script(pilot):
        await pilot.pause()
        worker = threading.Thread(target=app.post_item, args=(Text("first"),))
        worker.start()
        worker.join()                      # queued on the loop, not yet applied
        app.post_item(Text("second"))      # from the app thread, after it
        await until(pilot, lambda: len(_items(app)) == 2)
        assert [item.renderable.plain for item in _items(app)] == ["first", "second"]

    pilot_run(app, script)


def test_pane_width_is_cached_on_the_app_thread():
    app = PhilApp()
    got = []

    async def script(pilot):
        conversation = app.query_one(Conversation)
        await until(pilot, lambda: app.pane_width() == conversation.scrollable_content_region.width)
        await pilot.resize_terminal(60, 30)
        await until(pilot, lambda: app.pane_width() == conversation.scrollable_content_region.width < 60)
        narrow = app.pane_width()
        for n in range(100):                                   # a scrollbar takes its column
            app.post_item(Text(f"line {n}"))
        await until(pilot, lambda: conversation.max_scroll_y > 0)
        await pilot.pause()
        assert app.pane_width() == conversation.scrollable_content_region.width < narrow

        def no_queries(*args, **kwargs):
            raise AssertionError("pane_width must not read the widget tree")

        app.query_one = no_queries  # type: ignore[method-assign]
        worker = threading.Thread(target=lambda: got.append(app.pane_width()))
        worker.start()
        worker.join()
        del app.query_one
        assert got == [conversation.scrollable_content_region.width]

    pilot_run(app, script)
    assert app.pane_width() is None                  # closed


def test_item_height_is_cached_with_its_styled_text(monkeypatch):
    app = PhilApp()

    async def script(pilot):
        app.post_item(Text("a\nb\nc"))
        await until(pilot, lambda: len(_items(app)) == 1)
        await pilot.pause()
        item = _items(app)[0]
        width = item.size.width

        def no_split(*args, **kwargs):
            raise AssertionError("height must come from the cache")

        monkeypatch.setattr(Text, "split", no_split)
        assert item.get_content_height(item.size, item.size, width) == 3
        monkeypatch.undo()

    pilot_run(app, script)
