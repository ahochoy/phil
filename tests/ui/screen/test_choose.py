"""`ScreenIO.choose`: the controller's decision menus docked above the input box. Ported from
`TerminalIO.choose`'s tests in `tests/chat/test_terminal.py`: the same decisions and returns."""

import threading

from phil.chat.controller import TYPE, WAKE
from phil.chat.decision import Decision, Option
from phil.ui.screen.io import ScreenIO
from phil.ui.screen.widgets import Conversation, DecisionMenu, Item, LiveLine, PromptBox
from tests.ui.screen.pilot import pilot_run, render, until

D = Decision("pause", "⏸ r-1 needs you · setup failed", ("x",),
             (Option("Retry the task", "retry"), Option("Abort the run", "abort")))
D3 = Decision("question", "Pick one", (),
              (Option("First", "1"), Option("Second", "2"), Option("Third", "3")))


def _io():
    io = ScreenIO()
    return io, io.chat_io(lambda *args: None)


def _menu(io) -> DecisionMenu:
    return io.app.query_one(DecisionMenu)


def _box(io) -> PromptBox:
    return io.app.query_one(PromptBox)


async def _opened(pilot, io, number: int) -> None:
    """Wait until the `number`th menu of this IO shows and has focus."""
    menu = _menu(io)
    await until(pilot, lambda: menu.menu_id == number and menu.display and io.app.focused is menu)


async def _closed(pilot, io) -> None:
    await until(pilot, lambda: not _menu(io).display and io.app.focused is _box(io))


def _choosing(io, chat, *decisions):
    """Start the chat calling `choose` for each decision in turn; returns the list of results
    (a `KeyboardInterrupt` shows as "interrupted")."""
    got: list = []

    def fn():
        for decision in decisions:
            try:
                got.append(chat.choose("", decision))
            except KeyboardInterrupt:
                got.append("interrupted")

    io.start(fn)
    return got


def test_chat_io_offers_choose():
    io, chat = _io()
    assert chat.choose == io.choose


def test_choose_enter_picks_default():
    io, chat = _io()
    got = _choosing(io, chat, D, Decision("confirm", "t", (), D.options, default=1))

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("enter")
        await until(pilot, lambda: got == ["retry"])
        await _opened(pilot, io, 2)
        await pilot.press("enter")
        await until(pilot, lambda: got == ["retry", "abort"])

    pilot_run(io.app, script)


def test_choose_arrows_wrap():
    io, chat = _io()
    got = _choosing(io, chat, D, D, D3, D3)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("down", "enter")
        await until(pilot, lambda: got == ["abort"])
        await _opened(pilot, io, 2)
        await pilot.press("up", "up", "enter")          # 2 options: 0 -> 1 -> 0
        await until(pilot, lambda: len(got) == 2)
        await _opened(pilot, io, 3)
        await pilot.press("up", "enter")                # up from 0 wraps to the last
        await until(pilot, lambda: len(got) == 3)
        await _opened(pilot, io, 4)
        await pilot.press("down", "down", "down", "enter")  # down past the last wraps to the first
        await until(pilot, lambda: len(got) == 4)
        assert got == ["abort", "retry", "3", "1"]

    pilot_run(io.app, script)


def test_choose_digit_picks_at_once():
    io, chat = _io()
    got = _choosing(io, chat, D, D3)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("2")
        await until(pilot, lambda: got == ["abort"])
        await _opened(pilot, io, 2)
        await pilot.press("3")
        await until(pilot, lambda: got == ["abort", "3"])

    pilot_run(io.app, script)


def test_a_digit_past_the_options_and_letters_are_ignored():
    io, chat = _io()
    got = _choosing(io, chat, D)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("9", "z", "z", "tab")
        await pilot.pause()
        assert got == [] and io.app.focused is _menu(io)
        await pilot.press("enter")
        await until(pilot, lambda: got == ["retry"])
        assert _box(io).text == ""                      # nothing reached the input box

    pilot_run(io.app, script)


def test_choose_escape_returns_type():
    io, chat = _io()
    got = _choosing(io, chat, D)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("escape")
        await until(pilot, lambda: got == [TYPE])

    pilot_run(io.app, script)


def test_ctrl_d_in_the_menu_returns_none():
    io, chat = _io()
    got = _choosing(io, chat, D)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("ctrl+d")
        await until(pilot, lambda: got == [None])
        await _closed(pilot, io)

    pilot_run(io.app, script)


def test_ctrl_c_in_the_menu_interrupts_and_ask_still_works():
    io, chat = _io()
    got = []

    def fn():
        try:
            chat.choose("", D)
        except KeyboardInterrupt:
            got.append("interrupted")
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await _opened(pilot, io, 1)
        await pilot.press("ctrl+c")
        await until(pilot, lambda: got == ["interrupted"])
        await _closed(pilot, io)
        await until(pilot, lambda: io.asking)
        await pilot.press(*"abc", "enter")
        await until(pilot, lambda: len(got) == 2)
        assert got == ["interrupted", "abc"]

    pilot_run(io.app, script)


def test_a_pending_wake_returns_wake_before_the_menu():
    io, chat = _io()
    chat.wake()
    got = _choosing(io, chat, D, D)

    async def script(pilot):
        await until(pilot, lambda: got == [WAKE])
        await _opened(pilot, io, 1)                     # the woken call never opened a menu
        await pilot.press("enter")
        await until(pilot, lambda: got == [WAKE, "retry"])

    pilot_run(io.app, script)


async def _wake_on(pilot, io, chat, index: int) -> None:
    await until(pilot, lambda: io._choice == index)
    waker = threading.Thread(target=chat.wake)
    waker.start()
    waker.join()


def test_choose_wake_keeps_highlight_for_same_decision():
    io, chat = _io()
    got = _choosing(io, chat, D, D, D)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("down")
        await _wake_on(pilot, io, chat, 1)
        await until(pilot, lambda: got == [WAKE])
        await _opened(pilot, io, 2)
        await pilot.press("enter")
        await until(pilot, lambda: got == [WAKE, "abort"])   # the highlight was kept
        await _opened(pilot, io, 3)
        await pilot.press("enter")
        await until(pilot, lambda: got == [WAKE, "abort", "retry"])  # only into the one next menu

    pilot_run(io.app, script)


def test_choose_wake_resets_for_a_different_decision():
    io, chat = _io()
    got = _choosing(io, chat, D3, D, D3, D)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("down", "down")               # highlight 2 of D3's three options
        await _wake_on(pilot, io, chat, 2)
        await until(pilot, lambda: got == [WAKE])
        await _opened(pilot, io, 2)
        await pilot.press("enter")                      # a different, shorter menu: its own default
        await until(pilot, lambda: got == [WAKE, "retry"])
        await _opened(pilot, io, 3)
        await pilot.press("down")                       # 1: in range for D too, but D is another decision
        await _wake_on(pilot, io, chat, 1)
        await until(pilot, lambda: len(got) == 3)
        await _opened(pilot, io, 4)
        await pilot.press("enter")
        await until(pilot, lambda: got == [WAKE, "retry", WAKE, "retry"])

    pilot_run(io.app, script)


def test_a_typed_prompt_clears_a_carried_highlight():
    io, chat = _io()
    got = []

    def fn():
        got.append(chat.choose("", D))
        got.append(chat.ask("you › "))
        got.append(chat.choose("", D))

    async def script(pilot):
        io.start(fn)
        await _opened(pilot, io, 1)
        await pilot.press("down")
        await _wake_on(pilot, io, chat, 1)
        await until(pilot, lambda: io.asking)
        await pilot.press("x", "enter")
        await _opened(pilot, io, 2)
        await pilot.press("enter")
        await until(pilot, lambda: len(got) == 3)
        assert got == [WAKE, "x", "retry"]

    pilot_run(io.app, script)


def test_an_out_of_range_default_falls_back_to_the_first_option():
    io, chat = _io()
    got = _choosing(io, chat, Decision("confirm", "t", (), D.options, default=5))

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("enter")
        await until(pilot, lambda: got == ["retry"])

    pilot_run(io.app, script)


def test_choose_keeps_box_text():
    io, chat = _io()
    go = threading.Event()
    got = []

    def fn():
        go.wait(5)
        got.append(chat.choose("", D))
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await pilot.press(*"draft")
        await until(pilot, lambda: _box(io).text == "draft")
        go.set()
        await _opened(pilot, io, 1)
        assert _box(io).text == "draft" and not _box(io).can_focus
        await pilot.click(_box(io))                     # the box takes no keys while the menu shows
        await pilot.pause()
        assert io.app.focused is _menu(io)
        await pilot.press("x", "2")
        await until(pilot, lambda: got == ["abort"])
        await _closed(pilot, io)
        assert _box(io).text == "draft" and not io._lines   # nothing queued, the text kept
        await pilot.press("end", "!")                   # (the click above placed the cursor)
        await until(pilot, lambda: _box(io).text == "draft!")   # the box takes keys again
        await pilot.press("enter")
        await until(pilot, lambda: got == ["abort", "draft!"])

    pilot_run(io.app, script)


def test_lines_queued_before_the_menu_wait_for_the_next_ask():
    io, chat = _io()
    go = threading.Event()
    got = []

    def fn():
        go.wait(5)
        got.append(chat.choose("", D))
        got.append(chat.ask("you › "))

    async def script(pilot):
        io.start(fn)
        await pilot.press(*"early", "enter")
        await until(pilot, lambda: list(io._lines) == ["early"])
        go.set()
        await _opened(pilot, io, 1)
        await pilot.press("2")
        await until(pilot, lambda: len(got) == 2)
        assert got == ["abort", "early"]

    pilot_run(io.app, script)


def test_the_menu_shows_the_callout_and_leaves_nothing_in_the_conversation():
    io, chat = _io()
    got = _choosing(io, chat, D)

    async def script(pilot):
        await _opened(pilot, io, 1)
        await pilot.press("down")
        await until(pilot, lambda: "› 2 Abort the run" in render(_menu(io)))
        text = render(_menu(io))
        assert text.splitlines()[0].startswith("╭")
        assert any("Retry the task" in line and "›" not in line for line in text.splitlines())
        await pilot.press("enter")
        await until(pilot, lambda: got == ["abort"])
        await _closed(pilot, io)
        assert not io.app.query_one(Conversation).query(Item)

    pilot_run(io.app, script)


def test_choose_collapses_live_line_to_one_row():
    io, chat = _io()
    got = _choosing(io, chat, D)

    async def script(pilot):
        live = io.app.query_one(LiveLine)
        await _opened(pilot, io, 1)
        assert live.max_lines == 1
        await pilot.press("enter")
        await until(pilot, lambda: got == ["retry"])
        await until(pilot, lambda: live.max_lines is None)

    pilot_run(io.app, script)


def test_choose_returns_none_when_the_frame_closes():
    io, chat = _io()
    got = []
    futures = []

    async def script(pilot):
        futures.append(io.start(lambda: got.append(chat.choose("", D))))
        await _opened(pilot, io, 1)

    pilot_run(io.app, script)                         # the script ends: the app exits
    futures[0].result(timeout=5)
    assert got == [None]
