import threading

from rich.console import Console
from rich.text import Text

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
from tests.ui.screen.pilot import pilot_run, render, until


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
