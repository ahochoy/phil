import io
import threading
import time

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

from phil.chat.controller import TYPE, WAKE
from phil.chat.decision import Decision, Option
from phil.chat.terminal import LineIO, TerminalIO

TIMEOUT = 5.0

D = Decision("pause", "⏸ r-1 needs you · setup failed", ("x",),
             (Option("Retry the task", "retry"), Option("Abort the run", "abort")))
D3 = Decision("question", "Pick one", (),
              (Option("First", "1"), Option("Second", "2"), Option("Third", "3")))


def _ask(io_, prompt: str = "you › "):
    """Call `ask` on a helper thread and fail (instead of hanging) if it never returns."""
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = io_.ask(prompt)
        except BaseException as exc:  # surfaced below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(TIMEOUT)
    if thread.is_alive():
        pytest.fail("ask did not return (a lost wake?)")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _wake_when_prompting(terminal: TerminalIO, typed: str = "") -> threading.Thread:
    """Wake the prompt once it's running and (when `typed` is given) holds the typed text."""

    def poke() -> None:
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if terminal.prompting and (not typed or typed in terminal.session.app.current_buffer.text):
                break
            time.sleep(0.01)
        terminal.wake()

    thread = threading.Thread(target=poke, daemon=True)
    thread.start()
    return thread


def test_terminal_io_prompt_wake_keeps_typed_text_and_eof():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "toolbar text", input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)
        try:
            pipe.send_text("hello\n")
            assert _ask(io_) == "hello"

            thread = _wake_when_prompting(terminal)
            assert _ask(io_) is WAKE
            thread.join(TIMEOUT)

            pipe.send_text("half")
            thread = _wake_when_prompting(terminal, typed="half")
            assert _ask(io_) is WAKE
            thread.join(TIMEOUT)
            assert terminal._carried.text == "half"
            pipe.send_text(" done\n")
            assert _ask(io_) == "half done"  # typed text survives the wake

            pipe.send_text("\x04")  # Ctrl-D on an empty line
            assert _ask(io_) is None
        finally:
            terminal.close()


def test_a_wake_between_prompts_is_not_lost():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)
        try:
            io_.wake()  # no prompt active: remembered for the next ask
            assert _ask(io_) is WAKE
            pipe.send_text("x\n")
            assert _ask(io_) == "x"
        finally:
            terminal.close()


def test_a_wake_during_pre_run_is_not_lost():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)
        original = terminal._started

        def started_with_a_wake() -> None:
            terminal.wake()  # arrives while the prompt is starting, before it's marked active
            original()

        terminal._started = started_with_a_wake
        try:
            assert _ask(io_) is WAKE
        finally:
            terminal.close()


def test_woken_prompts_leave_no_lines_but_submitted_ones_stay():
    buffer = io.StringIO()
    output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80), term="xterm", enable_cpr=False)
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=output)
        io_ = terminal.chat_io(lambda *a: None)
        try:
            for _ in range(3):
                thread = _wake_when_prompting(terminal)
                assert _ask(io_) is WAKE
                thread.join(TIMEOUT)
            assert "\r\n" not in buffer.getvalue()
            pipe.send_text("kept\n")
            assert _ask(io_) == "kept"
            assert "\r\n" in buffer.getvalue()
        finally:
            terminal.close()


def test_terminal_io_runs_jobs_on_daemon_threads_and_close_does_not_wait():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)
        release = threading.Event()
        seen: list[threading.Thread] = []
        started = threading.Event()

        def blocking() -> None:
            seen.append(threading.current_thread())
            started.set()
            release.wait(TIMEOUT)

        io_.submit(blocking)
        assert started.wait(TIMEOUT)
        begin = time.monotonic()
        terminal.close()
        assert time.monotonic() - begin < 1.0
        assert seen[0].daemon and seen[0].name.startswith("phil-chat")
        release.set()


def test_at_most_three_jobs_run_at_once():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)
        release = threading.Event()
        lock = threading.Lock()
        running, peak, finished = [0], [0], threading.Semaphore(0)

        def job() -> None:
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            release.wait(TIMEOUT)
            with lock:
                running[0] -= 1
            finished.release()

        begin = time.monotonic()
        for _ in range(5):
            io_.submit(job)
        assert time.monotonic() - begin < 1.0  # submit never blocks the main thread
        time.sleep(0.2)
        assert peak[0] == 3
        release.set()
        for _ in range(5):
            assert finished.acquire(timeout=TIMEOUT)
        assert peak[0] == 3
        terminal.close()


def test_a_failing_toolbar_does_not_break_the_prompt():
    def broken() -> str:
        raise RuntimeError("boom")

    with create_pipe_input() as pipe:
        terminal = TerminalIO(broken, input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)
        try:
            pipe.send_text("ok\n")
            assert _ask(io_, "› ") == "ok"
        finally:
            terminal.close()


def test_line_io_reads_lines_and_eof(monkeypatch):
    console = Console(record=True)
    answers = iter(["hi"])

    def fake_input(prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(console, "input", fake_input)
    line = LineIO(console)
    io_ = line.chat_io(lambda *a: None)
    assert io_.ask("› ") == "hi"
    assert io_.ask("› ") is None
    ran: list[int] = []
    io_.submit(lambda: ran.append(1))  # inline
    assert ran == [1]
    io_.wake()  # no-op
    assert line.run(lambda: 7) == 7
    line.close()


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")
        time.sleep(0.01)


def test_a_chat_runs_real_jobs_through_submit_post_and_wake(calc_repo):
    """End to end on real threads: jobs run via TerminalIO.submit, post events, and wake the prompt."""
    from phil.chat.controller import ChatController
    from phil.config import PhilConfig
    from phil.repo import resolve_repo
    from phil.store.db import connect
    from phil.store.paths import ProjectPaths
    from phil.ui.theme import make_console
    from tests.chat.conftest import ChatFactory, critique, goal, plan
    from tests.chat.test_controller import ManualWatcher
    from tests.helpers import TEST_MODELS

    info = resolve_repo(calc_repo)
    console = make_console(record=True, width=120)
    spawned: list = []
    box: dict = {}
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda root, run_id, mode, decision=None: spawned.append((run_id, mode)))

        def chat() -> None:
            # The controller (and its connection) live on this thread, as they would on the main one.
            conn = connect(ProjectPaths(info.slug).db_path)
            try:
                controller = ChatController(
                    info, PhilConfig(models=TEST_MODELS), conn, console, io_, start_pr_monitor=False,
                    factory=ChatFactory({"intake": [goal()], "architect": [plan()], "critic": [critique()]}),
                    watcher_factory=lambda run_id: ManualWatcher(
                        ProjectPaths(info.slug), run_id, box["controller"].post,
                        alive=lambda r: False, starting=lambda e: False,
                    ),
                )
                box["controller"] = controller
                controller.run()
            except BaseException as exc:  # surfaced below
                box["error"] = exc

        thread = threading.Thread(target=chat, daemon=True)
        thread.start()
        try:
            _until(lambda: "controller" in box and terminal.prompting, "the first prompt")
            pipe.send_text("add subtract\n")
            _until(lambda: box["controller"].stage == "approval" or "error" in box, "the plan")
            pipe.send_text("y\n")
            _until(lambda: spawned or "error" in box, "the run to start")
            _until(lambda: terminal.prompting, "the running prompt")
            pipe.send_text("\x04")
            thread.join(TIMEOUT)
            assert not thread.is_alive(), "the chat did not end"
        finally:
            terminal.close()
    if "error" in box:
        raise box["error"]
    text = console.export_text()
    assert "Details: /more 1" not in text, text  # no failure callout
    assert "Plan CALC v1" in text
    assert spawned and spawned[0][1] == "start"


def test_prompt_message_puts_the_live_row_above_the_input():
    from prompt_toolkit.formatted_text import to_plain_text

    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", live_row=lambda: "⠋ T1 · implementer · run pytest · 3s",
                              input=pipe, output=DummyOutput())
        assert to_plain_text(terminal._message("you › ")()) == "⠋ T1 · implementer · run pytest · 3s\nyou › "
        quiet = TerminalIO(lambda: "", live_row=lambda: "", input=pipe, output=DummyOutput())
        assert to_plain_text(quiet._message("you › ")()) == "you › "


def test_prompt_message_accepts_fragments_for_the_live_row():
    """When the live row callable returns fragments (a list), `_message` uses them as-is, with a
    newline after the last one; an empty list shows no row at all."""
    from prompt_toolkit.formatted_text import to_plain_text

    with create_pipe_input() as pipe:
        rows = [("class:phil.agent", "⠋"), ("", " CALC-002 · implementer · 3s")]
        terminal = TerminalIO(lambda: "", live_row=lambda: rows, input=pipe, output=DummyOutput())
        assert to_plain_text(terminal._message("you › ")()) == "⠋ CALC-002 · implementer · 3s\nyou › "
        empty = TerminalIO(lambda: "", live_row=lambda: [], input=pipe, output=DummyOutput())
        assert to_plain_text(empty._message("you › ")()) == "you › "


def test_a_finished_prompt_leaves_the_live_row_out():
    """The final redraw of a submitted prompt has no live row, so none is left in the scrollback."""
    from prompt_toolkit.formatted_text import to_plain_text

    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", live_row=lambda: "⠋ T1 · implementer · run pytest · 3s",
                              input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)
        original = terminal._message
        seen: list[tuple[bool, str]] = []

        def recording(prompt):
            inner = original(prompt)

            def message():
                out = inner()
                seen.append((terminal.session.app.is_done, to_plain_text(out)))
                return out

            return message

        terminal._message = recording
        try:
            pipe.send_text("x\n")
            assert _ask(io_) == "x"
            row = "⠋ T1 · implementer · run pytest · 3s\nyou › "
            assert (False, row) in seen  # while the prompt runs
            assert seen[-1] == (True, "you › ")  # the final, done redraw: what stays in the scrollback
            assert all(text == row for done, text in seen if not done)
        finally:
            terminal.close()


def test_a_wake_keeps_the_cursor_position():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io_ = terminal.chat_io(lambda *a: None)

        def poke() -> None:
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline:
                if terminal.prompting:
                    document = terminal.session.app.current_buffer.document
                    if document.text == "abcd" and document.cursor_position == 2:
                        break
                time.sleep(0.01)
            terminal.wake()

        try:
            pipe.send_text("abcd\x1b[D\x1b[D")  # type, then move the cursor two cells left
            thread = threading.Thread(target=poke, daemon=True)
            thread.start()
            assert _ask(io_) is WAKE
            thread.join(TIMEOUT)
            assert (terminal._carried.text, terminal._carried.cursor_position) == ("abcd", 2)
            pipe.send_text("X\n")
            assert _ask(io_) == "abXcd"  # typing resumes where the cursor was
        finally:
            terminal.close()


def test_a_failing_live_row_never_takes_the_prompt_down():
    from prompt_toolkit.formatted_text import to_plain_text

    def broken() -> str:
        raise RuntimeError("boom")

    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", live_row=broken, input=pipe, output=DummyOutput())
        assert to_plain_text(terminal._message("you › ")()) == "you › "
        plain = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        assert to_plain_text(plain._message("you › ")()) == "you › "


# --- decision mode -------------------------------------------------------------------------------


def _choose(terminal: TerminalIO, decision: Decision, prompt: str = ""):
    """Call `choose` on a helper thread and fail (instead of hanging) if it never returns."""
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = terminal.choose(prompt, decision)
        except BaseException as exc:  # surfaced below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(TIMEOUT)
    if thread.is_alive():
        pytest.fail("choose did not return")
    if "error" in box:
        raise box["error"]
    return box["result"]


@pytest.fixture
def pipe():
    with create_pipe_input() as pipe_input:
        yield pipe_input


@pytest.fixture
def terminal_io(pipe):
    terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
    yield terminal
    terminal.close()


def test_chat_io_offers_choose(terminal_io):
    assert terminal_io.chat_io(lambda *a: None).choose == terminal_io.choose


def test_enter_picks_the_default(terminal_io, pipe):
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "retry"
    pipe.send_text("\r")
    assert _choose(terminal_io, Decision("confirm", "t", (), D.options, default=1)) == "abort"


def test_down_then_enter_picks_the_next_and_up_wraps(terminal_io, pipe):
    pipe.send_text("\x1b[B\r")
    assert _choose(terminal_io, D) == "abort"
    pipe.send_text("\x1b[A\x1b[A\r")
    assert _choose(terminal_io, D) == "retry"  # 2 options: up twice from 0 goes 0 -> 1 -> 0
    pipe.send_text("\x1b[A\r")
    assert _choose(terminal_io, D3) == "3"  # up from 0 wraps to the last (index 2)
    pipe.send_text("\x1b[B\x1b[B\x1b[B\r")
    assert _choose(terminal_io, D3) == "1"  # down past the last wraps to the first


def test_a_number_picks_at_once(terminal_io, pipe):
    pipe.send_text("2")
    assert _choose(terminal_io, D) == "abort"
    pipe.send_text("3")
    assert _choose(terminal_io, D3) == "3"


def test_a_number_past_the_options_is_ignored(terminal_io, pipe):
    pipe.send_text("9\r")
    assert _choose(terminal_io, D) == "retry"


def test_typed_letters_are_ignored_in_the_menu(terminal_io, pipe):
    pipe.send_text("zz\r")
    assert _choose(terminal_io, D) == "retry"
    assert terminal_io.session.default_buffer.text == ""  # nothing reached the input buffer
    pipe.send_text("\x1b[200~pasted\x1b[201~\x1b[B\r")  # a bracketed paste
    assert _choose(terminal_io, D) == "abort"
    assert terminal_io.session.default_buffer.text == ""


def test_escape_returns_type(terminal_io, pipe):
    pipe.send_text("\x1b")
    begin = time.monotonic()
    assert _choose(terminal_io, D) is TYPE
    assert time.monotonic() - begin < 2.0


def test_a_lone_escape_is_flushed_quickly():
    """A production-built TerminalIO doesn't wait prompt_toolkit's default 0.5s for a longer sequence."""
    with create_pipe_input() as pipe_input:
        terminal = TerminalIO(lambda: "", input=pipe_input, output=DummyOutput())
        assert terminal.session.app.ttimeoutlen <= 0.1


def test_ctrl_c_in_the_menu_leaves_a_normal_prompt(terminal_io, pipe):
    pipe.send_text("\x03")
    with pytest.raises(KeyboardInterrupt):
        _choose(terminal_io, D)
    assert terminal_io.session.key_bindings is None  # no menu bindings left behind
    assert not terminal_io.session.default_buffer.read_only()
    io_ = terminal_io.chat_io(lambda *a: None)
    pipe.send_text("abc\n")
    assert _ask(io_) == "abc"


def test_ask_still_takes_typed_text_after_a_menu(terminal_io, pipe):
    pipe.send_text("\x1b")
    assert _choose(terminal_io, D) is TYPE
    io_ = terminal_io.chat_io(lambda *a: None)
    pipe.send_text("abc\n")
    assert _ask(io_) == "abc"


def test_the_menu_is_erased_but_a_later_submitted_line_stays():
    buffer = io.StringIO()
    output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80), term="xterm", enable_cpr=False)
    with create_pipe_input() as pipe_input:
        terminal = TerminalIO(lambda: "", input=pipe_input, output=output)
        io_ = terminal.chat_io(lambda *a: None)
        erased: list[bool] = []
        renderer = terminal.session.app.renderer
        original = renderer.erase

        def recording_erase(*args, **kwargs):
            erased.append(True)
            return original(*args, **kwargs)

        renderer.erase = recording_erase
        try:
            pipe_input.send_text("\r")
            assert _choose(terminal, D) == "retry"
            assert erased == [True]  # the docked box is erased, not left in the scrollback
            before = len(buffer.getvalue())
            pipe_input.send_text("kept\n")
            assert _ask(io_) == "kept"
            assert erased == [True]  # the submitted line is not erased
            assert "\r\n" in buffer.getvalue()[before:]
        finally:
            terminal.close()


def test_ctrl_d_in_the_menu_returns_none(terminal_io, pipe):
    pipe.send_text("\x04")
    assert _choose(terminal_io, D) is None


def test_a_pending_wake_returns_wake_before_the_menu(terminal_io, pipe):
    terminal_io.wake()
    assert _choose(terminal_io, D) is WAKE
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "retry"


def test_a_wake_keeps_the_highlight(terminal_io, pipe):
    pipe.send_text("\x1b[B")

    def poke() -> None:
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if terminal_io.prompting and terminal_io._choice == 1:
                break
            time.sleep(0.01)
        terminal_io.wake()

    thread = threading.Thread(target=poke, daemon=True)
    thread.start()
    assert _choose(terminal_io, D) is WAKE
    thread.join(TIMEOUT)
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "abort"  # the highlight was kept
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "retry"  # and only carried into the one next menu


def _wake_on_highlight(terminal: TerminalIO, index: int) -> threading.Thread:
    def poke() -> None:
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if terminal.prompting and terminal._choice == index:
                break
            time.sleep(0.01)
        terminal.wake()

    thread = threading.Thread(target=poke, daemon=True)
    thread.start()
    return thread


def test_a_carried_highlight_only_applies_to_the_same_decision(terminal_io, pipe):
    pipe.send_text("\x1b[B\x1b[B")  # highlight 2 of D3's three options
    thread = _wake_on_highlight(terminal_io, 2)
    assert _choose(terminal_io, D3) is WAKE
    thread.join(TIMEOUT)
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "retry"  # a different, shorter menu opens on its own default
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "retry"  # and the prompt still works
    pipe.send_text("\x1b[B")  # highlight 1 of D3: in range for D too, but D is another decision
    thread = _wake_on_highlight(terminal_io, 1)
    assert _choose(terminal_io, D3) is WAKE
    thread.join(TIMEOUT)
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "retry"


def test_a_typed_prompt_clears_a_carried_highlight(terminal_io, pipe):
    pipe.send_text("\x1b[B")
    thread = _wake_on_highlight(terminal_io, 1)
    assert _choose(terminal_io, D) is WAKE
    thread.join(TIMEOUT)
    pipe.send_text("x\n")
    assert _ask(terminal_io.chat_io(lambda *a: None)) == "x"
    pipe.send_text("\r")
    assert _choose(terminal_io, D) == "retry"


def test_an_out_of_range_default_falls_back_to_the_first_option(terminal_io, pipe):
    pipe.send_text("\r")
    assert _choose(terminal_io, Decision("confirm", "t", (), D.options, default=5)) == "retry"


def test_prompt_toolkit_styles_cover_the_real_theme():
    from phil.ui.theme import PHIL_THEME, prompt_toolkit_styles

    rules = prompt_toolkit_styles()  # raises if the theme has a style the prompt can't show
    toolbar_styles = {
        "phil.muted", "phil.warn", "phil.error", "phil.gate.pass", "phil.id", "phil.cost", "phil.agent", "phil.sub",
    }
    assert {n for n in PHIL_THEME.styles if n.startswith("phil.callout.")} | {"live"} | toolbar_styles == set(rules)


def test_the_menu_message_docks_the_callout_under_the_live_row():
    from prompt_toolkit.formatted_text import to_plain_text

    with create_pipe_input() as pipe_input:
        terminal = TerminalIO(lambda: "", live_row=lambda: "⠋ T1 · working", input=pipe_input,
                              output=DummyOutput())
        terminal._choice = 1
        text = to_plain_text(terminal._decision_message(D)())
        lines = text.splitlines()
        assert lines[0] == "⠋ T1 · working"
        assert lines[1].startswith("╭")
        assert any("› 2 Abort the run" in line for line in lines)
        assert any("Retry the task" in line and "›" not in line for line in lines)


def test_prompt_style_colours_the_callout_and_the_live_row():
    from phil.chat.terminal import prompt_style

    style = prompt_style()
    pause = style.get_attrs_for_style_str("class:phil.callout.title.pause")
    assert pause.bold and pause.color == "ansiyellow"
    assert style.get_attrs_for_style_str("class:phil.callout.border.failure").color == "ansired"
    selected = style.get_attrs_for_style_str("class:phil.callout.selected")
    assert selected.bold and selected.reverse
    assert style.get_attrs_for_style_str("class:phil.callout.hint").dim
    assert style.get_attrs_for_style_str("class:live").dim


def test_rich_styles_translate_to_prompt_toolkit():
    from phil.ui.theme import prompt_toolkit_style

    assert prompt_toolkit_style("bold yellow") == "bold ansiyellow"
    assert prompt_toolkit_style("bold cyan") == "bold ansicyan"
    assert prompt_toolkit_style("red") == "ansired"
    assert prompt_toolkit_style("bold reverse") == "bold reverse"
    assert prompt_toolkit_style("dim") == "dim"
    assert prompt_toolkit_style("default") == ""


def _line_io(monkeypatch, *answers: str) -> tuple[LineIO, Console]:
    console = Console(record=True, width=80)
    pending = iter(answers)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(pending)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(console, "input", fake_input)
    return LineIO(console), console


def test_line_io_choose_prints_numbered_and_maps_numbers(monkeypatch):
    line, console = _line_io(monkeypatch, "2", "abort", "anything")
    io_ = line.chat_io(lambda *a: None)
    assert io_.choose("› ", D) == "abort"
    text = console.export_text()
    assert "1 Retry the task" in text
    assert "2 Abort the run" in text
    assert io_.choose("› ", D) == "abort"
    assert io_.choose("› ", D) == "anything"
    assert io_.choose("› ", D) is None  # EOF


def test_line_io_choose_leaves_out_of_range_numbers_as_text(monkeypatch):
    line, _ = _line_io(monkeypatch, "3", "0")
    assert line.choose("› ", D) == "3"
    assert line.choose("› ", D) == "0"
