import threading
import time

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from phil.chat.controller import WAKE
from phil.chat.terminal import LineIO, TerminalIO


def _wake_when_prompting(terminal: TerminalIO) -> threading.Thread:
    def poke() -> None:
        deadline = time.monotonic() + 5
        while not terminal.prompting and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        terminal.wake()

    thread = threading.Thread(target=poke)
    thread.start()
    return thread


def test_terminal_io_prompt_wake_keeps_typed_text_and_eof():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "toolbar text", input=pipe, output=DummyOutput())
        io = terminal.chat_io(lambda *a: None)
        try:
            pipe.send_text("hello\n")
            assert io.ask("you › ") == "hello"

            thread = _wake_when_prompting(terminal)
            assert io.ask("you › ") is WAKE
            thread.join()

            pipe.send_text("half")
            thread = _wake_when_prompting(terminal)
            assert io.ask("you › ") is WAKE
            thread.join()
            pipe.send_text(" done\n")
            assert io.ask("you › ") == "half done"  # typed text survives the wake

            pipe.send_text("\x04")  # Ctrl-D on an empty line
            assert io.ask("you › ") is None
        finally:
            terminal.close()


def test_a_wake_between_prompts_is_not_lost():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io = terminal.chat_io(lambda *a: None)
        try:
            io.wake()  # no prompt active: remembered for the next ask
            assert io.ask("you › ") is WAKE
            pipe.send_text("x\n")
            assert io.ask("you › ") == "x"
        finally:
            terminal.close()


def test_terminal_io_runs_jobs_on_worker_threads():
    with create_pipe_input() as pipe:
        terminal = TerminalIO(lambda: "", input=pipe, output=DummyOutput())
        io = terminal.chat_io(lambda *a: None)
        seen: list[str] = []
        done = threading.Event()
        io.submit(lambda: (seen.append(threading.current_thread().name), done.set()))
        assert done.wait(5)
        terminal.close()
        assert seen[0].startswith("phil-chat")


def test_a_failing_toolbar_does_not_break_the_prompt():
    def broken() -> str:
        raise RuntimeError("boom")

    with create_pipe_input() as pipe:
        terminal = TerminalIO(broken, input=pipe, output=DummyOutput())
        io = terminal.chat_io(lambda *a: None)
        try:
            pipe.send_text("ok\n")
            assert io.ask("› ") == "ok"
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
    io = line.chat_io(lambda *a: None)
    assert io.ask("› ") == "hi"
    assert io.ask("› ") is None
    ran: list[int] = []
    io.submit(lambda: ran.append(1))  # inline
    assert ran == [1]
    io.wake()  # no-op
    assert line.run(lambda: 7) == 7
    line.close()
