import threading
import time

import pytest
from rich.text import Text
from textual import events

from phil.chat.controller import WAKE
from phil.ui.screen.app import PhilApp
from phil.ui.screen.io import MAX_JOBS, ScreenIO
from phil.ui.screen.widgets import Conversation, Item, PromptBox
from tests.ui.screen.pilot import drag, pilot_run, until


def _io():
    io = ScreenIO()
    return io, io.chat_io(lambda *args: None)


def _box(io) -> PromptBox:
    return io.app.query_one(PromptBox)


def test_ask_returns_the_typed_line():
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        await pilot.press(*"hello", "enter")
        await until(pilot, lambda: got)
        assert got == ["hello"]

    pilot_run(io.app, script)


def test_the_prompt_shows_on_the_box_not_in_it():
    io, chat = _io()

    async def script(pilot):
        io.start(lambda: chat.ask("goal › "))
        await until(pilot, lambda: _box(io).border_title == "goal ›")
        assert _box(io).text == ""

    pilot_run(io.app, script)


def test_typing_before_ask_is_queued():
    io, chat = _io()
    go = threading.Event()
    got = []

    def fn():
        go.wait(5)
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await pilot.press(*"early", "enter")
        await until(pilot, lambda: _box(io).text == "")
        await pilot.pause()
        assert not io.asking and list(io._lines) == ["early"]   # queued while busy
        go.set()
        await until(pilot, lambda: got)
        assert got == ["early"]

    pilot_run(io.app, script)


def test_empty_enter_sends_an_empty_line():
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == [""]

    pilot_run(io.app, script)


def test_wake_mid_word_keeps_text_and_cursor():      # Review Focus 1
    io, chat = _io()
    got = []

    def fn():
        got.append(chat.ask("you › "))
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await until(pilot, lambda: io.asking)
        await pilot.press(*"hel")
        await pilot.pause()
        waker = threading.Thread(target=chat.wake)
        waker.start()
        waker.join()
        await until(pilot, lambda: got)
        assert got == [WAKE]
        await until(pilot, lambda: io.asking)
        assert _box(io).text == "hel" and _box(io).cursor_location == (0, 3)
        await pilot.press(*"lo", "enter")
        await until(pilot, lambda: len(got) == 2)
        assert got == [WAKE, "hello"]

    pilot_run(io.app, script)


def test_wake_before_ask_is_remembered():
    io, chat = _io()
    got = []
    chat.wake()

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: got)
        assert got == [WAKE]

    pilot_run(io.app, script)


def test_prefill_puts_text_in_the_box():
    io, chat = _io()
    got = []

    def fn():
        chat.prefill("draft")
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await until(pilot, lambda: _box(io).text == "draft")
        assert _box(io).cursor_location == (0, 5)
        await pilot.press("!", "enter")
        await until(pilot, lambda: got)
        assert got == ["draft!"]

    pilot_run(io.app, script)


def test_ctrl_d_on_empty_box_is_eof():
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        await pilot.press("x", "left", "ctrl+d")   # not empty: Ctrl+D edits, no EOF
        await pilot.pause()
        assert got == [] and _box(io).text == ""
        await pilot.press("ctrl+d")
        await until(pilot, lambda: got)
        assert got == [None]

    pilot_run(io.app, script)


def test_ctrl_c_interrupts_a_blocked_ask():
    io, chat = _io()
    got = []

    def fn():
        try:
            chat.ask("you › ")
        except KeyboardInterrupt:
            got.append("interrupted")

    async def script(pilot):
        io.start(fn)
        await until(pilot, lambda: io.asking)
        await pilot.press("ctrl+c")
        await until(pilot, lambda: got)
        assert got == ["interrupted"]

    pilot_run(io.app, script)


def test_ctrl_c_while_busy_raises_at_next_ask():
    io, chat = _io()
    go = threading.Event()
    got = []

    def fn():
        go.wait(5)
        try:
            chat.ask("you › ")
        except KeyboardInterrupt:
            got.append("interrupted")
        got.append(chat.ask("you › "))               # one Ctrl+C, one interrupt

    async def script(pilot):
        io.start(fn)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not io.asking and io._interrupt_held       # held while busy
        go.set()
        await until(pilot, lambda: got)
        assert got == ["interrupted"]
        await until(pilot, lambda: io.asking)
        await pilot.press(*"ok", "enter")
        await until(pilot, lambda: len(got) == 2)
        assert got == ["interrupted", "ok"]

    pilot_run(io.app, script)


def test_ctrl_c_drops_lines_typed_ahead():
    io, chat = _io()
    go = threading.Event()
    got = []

    def fn():
        go.wait(5)
        try:
            chat.ask("you › ")
        except KeyboardInterrupt:
            got.append("interrupted")
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await pilot.press(*"fix the tests", "enter")
        await until(pilot, lambda: list(io._lines) == ["fix the tests"])
        await pilot.press(*"draft", "ctrl+c")
        await pilot.pause()
        assert not io.asking and io._interrupt_held and not io._lines
        go.set()
        await until(pilot, lambda: got == ["interrupted"])
        await until(pilot, lambda: io.asking)
        await pilot.pause()
        assert got == ["interrupted"]                   # the cancelled line is gone: ask blocks
        assert _box(io).text == "draft"                 # text still in the box is untouched
        await pilot.press("enter")
        await until(pilot, lambda: len(got) == 2)
        assert got == ["interrupted", "draft"]

    pilot_run(io.app, script)


def test_ctrl_c_keeps_a_queued_eof():
    io, chat = _io()
    go = threading.Event()
    got = []

    def fn():
        go.wait(5)
        try:
            chat.ask("you › ")
        except KeyboardInterrupt:
            got.append("interrupted")
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await pilot.press(*"x", "enter", "ctrl+d", "ctrl+c")
        await until(pilot, lambda: io._interrupt_held)
        go.set()
        await until(pilot, lambda: len(got) == 2)
        assert got == ["interrupted", None]

    pilot_run(io.app, script)


def test_queued_lines_are_not_delivered_after_the_frame_closes():
    io, chat = _io()
    go = threading.Event()
    got = []
    futures = []

    def fn():
        go.wait(5)
        got.append(chat.ask("you › "))

    async def script(pilot):
        futures.append(io.start(fn))
        await pilot.press(*"late", "enter")
        await until(pilot, lambda: list(io._lines) == ["late"])

    pilot_run(io.app, script)
    go.set()
    futures[0].result(timeout=5)
    assert got == [None]


def test_held_interrupt_is_dropped_at_exit():         # Review Focus 3
    io, chat = _io()
    go = threading.Event()
    futures = []

    def fn():
        go.wait(5)
        return "done"

    async def script(pilot):
        futures.append(io.start(fn))
        await pilot.press("ctrl+c")
        await pilot.pause()
        go.set()
        await until(pilot, lambda: futures[0].done())

    pilot_run(io.app, script)
    assert futures[0].exception() is None and futures[0].result() == "done"
    assert not io.app.is_running
    assert chat.ask("you › ") is None                 # the frame is gone: EOF, not a stale interrupt


def test_discard_interrupt_drops_a_held_ctrl_c():     # a /chat switch: the next chat starts clean
    io, chat = _io()
    io.interrupt()                                    # Ctrl+C while no ask was waiting
    io.discard_interrupt()
    io.line_entered("next")
    assert chat.ask("you › ") == "next"


def test_fn_returning_exits_the_app():
    io, chat = _io()
    seen = []

    async def script(pilot):
        io.start(lambda: None)
        await until(pilot, lambda: not io.app.is_running)
        seen.append("exited")

    pilot_run(io.app, script)
    assert seen == ["exited"]


def test_ask_returns_eof_when_the_frame_closes():
    io, chat = _io()
    got = []
    futures = []

    async def script(pilot):
        futures.append(io.start(lambda: got.append(chat.ask("you › "))))
        await until(pilot, lambda: io.asking)

    pilot_run(io.app, script)                        # the script ends: the app exits
    futures[0].result(timeout=5)
    assert got == [None]


def test_ctrl_c_with_selection_copies_not_interrupts():
    io, chat = _io()
    got = []

    def fn():
        try:
            got.append(chat.ask("you › "))
        except KeyboardInterrupt:
            got.append("interrupted")

    async def script(pilot):
        io.app.post_item(Text("hello selectable world"))
        await until(pilot, lambda: len(io.app.query_one(Conversation).query(Item)) == 1)
        io.start(fn)
        await until(pilot, lambda: io.asking)
        await drag(pilot, io.app.query_one(Item), (0, 0), (15, 0))
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert "hello selectable" in io.app.clipboard
        assert got == [] and io.asking

    pilot_run(io.app, script)


def test_ctrl_c_with_a_selection_in_the_box_copies_it():
    io, chat = _io()
    got = []

    def fn():
        try:
            got.append(chat.ask("you › "))
        except KeyboardInterrupt:
            got.append("interrupted")

    async def script(pilot):
        io.start(fn)
        await until(pilot, lambda: io.asking)
        await pilot.press(*"abc", "shift+left", "shift+left")
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert io.app.clipboard == "bc"
        assert got == [] and io.asking

    pilot_run(io.app, script)


def test_copy_puts_text_on_the_clipboard_while_running():
    io, chat = _io()
    assert chat.copy("early") is False               # not mounted yet

    async def script(pilot):
        await pilot.pause()
        assert chat.copy("some text") is True
        await until(pilot, lambda: io.app.clipboard == "some text")

    pilot_run(io.app, script)
    assert chat.copy("late") is False


def test_paste_keeps_newlines_and_does_not_send():   # Review Focus 4
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        io.app.post_message(events.Paste("a\nb"))    # as the driver sends a bracketed paste
        await until(pilot, lambda: _box(io).text == "a\nb")
        await pilot.pause()
        assert got == [] and io.asking
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == ["a\nb"]

    pilot_run(io.app, script)


@pytest.mark.parametrize("key", ["alt+enter", "shift+enter", "ctrl+enter"])
def test_alt_enter_adds_a_line(key):
    io, chat = _io()
    got = []

    async def script(pilot):
        io.start(lambda: got.append(chat.ask("you › ")))
        await until(pilot, lambda: io.asking)
        await pilot.press("a", key, "b")
        await pilot.pause()
        assert _box(io).text == "a\nb" and got == []
        await pilot.press("enter")
        await until(pilot, lambda: got)
        assert got == ["a\nb"]

    pilot_run(io.app, script)


def test_submit_runs_at_most_three_jobs_at_once():
    io, chat = _io()
    release = threading.Event()
    lock = threading.Lock()
    running, peak, finished = [0], [0], [0]

    def job():
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        release.wait(5)
        with lock:
            running[0] -= 1
            finished[0] += 1

    started = time.monotonic()
    threads = [chat.submit(job) for _ in range(5)]
    assert time.monotonic() - started < 1            # submit never blocks
    assert all(thread.daemon for thread in threads)
    deadline = time.monotonic() + 5
    while running[0] < MAX_JOBS and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)
    assert MAX_JOBS == 3 and running[0] == 3 and peak[0] == 3
    release.set()
    for thread in threads:
        thread.join(5)
    assert finished[0] == 5 and peak[0] == 3


def _headless(io, order):
    real_run = PhilApp.run

    def run(*args, **kwargs):
        try:
            return real_run(io.app, headless=True, size=(100, 30))
        finally:
            order.append("app returned")

    io.app.run = run


def test_run_returns_fn_value():
    io, chat = _io()
    order = []
    _headless(io, order)
    assert io.run(lambda: threading.current_thread().name) == "phil-chat"
    assert order == ["app returned"] and not io.app.is_running


def test_run_reraises_fn_exception_after_exit():
    io, chat = _io()
    order = []
    _headless(io, order)

    def fn():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        try:
            io.run(fn)
        finally:
            order.append("raised")
    assert order == ["app returned", "raised"]
    assert not io.app.is_running


def test_run_raises_when_the_frame_crashes(capfd):
    io, chat = _io()
    order = []
    _headless(io, order)

    def boom():
        raise ValueError("handler failed")

    def fn():
        io.app.call_from_thread(io.app.call_later, boom)
        return chat.ask("you › ")                      # None once the crashed frame closes

    with pytest.raises(RuntimeError, match="stopped unexpectedly") as caught:
        io.run(fn)
    assert isinstance(caught.value.__cause__, ValueError)
    assert order == ["app returned"] and not io.app.is_running


def test_width_is_the_conversation_pane_content_width():
    io, chat = _io()
    assert io.width() == 80                           # before mount

    async def script(pilot):
        await pilot.pause()
        assert io.width() == io.app.query_one(Conversation).scrollable_content_region.width
        assert 90 <= io.width() < 100
        assert io.console.width == io.width()
        io.console.print("printed")
        await until(pilot, lambda: len(io.app.query_one(Conversation).query(Item)) == 1)

    pilot_run(io.app, script)
