import io
import threading
import time

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

from phil.chat.controller import WAKE
from phil.chat.terminal import LineIO, TerminalIO

TIMEOUT = 5.0


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
            assert terminal._carried == "half"
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
    from phil.agents.fake import ScriptedAgentFactory
    from phil.chat.controller import ChatController
    from phil.config import PhilConfig
    from phil.repo import resolve_repo
    from phil.store.db import connect
    from phil.store.paths import ProjectPaths
    from phil.ui.theme import make_console
    from tests.chat.conftest import critique, goal, plan
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
                    factory=ScriptedAgentFactory({"intake": [goal()], "architect": [plan()], "critic": [critique()]}),
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
    assert "Phil couldn't finish that" not in text, text
    assert "Plan CALC v1" in text
    assert spawned and spawned[0][1] == "start"
