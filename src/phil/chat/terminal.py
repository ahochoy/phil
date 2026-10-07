"""The chat's terminal IO: a prompt_toolkit prompt with a bottom toolbar (TTY), or plain lines.

This is the only module that imports prompt_toolkit; `phil.cli.main` imports it lazily.
"""

import threading
from collections.abc import Callable
import itertools
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

from phil.chat.controller import WAKE, ChatIO

MAX_JOBS = 3
Spawn = Callable[[Path, str, str, dict | None], object]


class LineIO:
    """Non-TTY chat IO (piped input, CI): read lines with the console, run jobs inline, never wake."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def chat_io(self, spawn: Spawn) -> ChatIO:
        def ask(prompt: str) -> str | None:
            try:
                return self.console.input(f"[phil.user]{escape(prompt)}[/]")
            except EOFError:
                return None

        return ChatIO(ask=ask, spawn=spawn)

    def run(self, fn: Callable[[], Any]) -> Any:
        return fn()

    def close(self) -> None:
        pass


class TerminalIO:
    """A prompt with a live bottom toolbar; background events wake it without losing typed text.

    `ask` runs on the main thread. `wake` may be called from any thread: it exits the active prompt
    with `WAKE`, carrying the typed text into the next prompt; with no prompt active it's remembered,
    so an event posted between the controller's drain and the next prompt still wakes it.
    """

    def __init__(
        self, toolbar: Callable[[], str], live_row: Callable[[], str] | None = None, *, input=None, output=None
    ) -> None:
        self._toolbar = toolbar
        self._live_row = live_row  # the running step, shown on its own line above the input
        self.session: PromptSession = PromptSession(
            bottom_toolbar=self._render_toolbar, refresh_interval=0.5, input=input, output=output
        )
        self._lock = threading.Lock()
        self._active = False  # a prompt is running (between its pre_run and its return)
        self._wake_pending = False
        # What was typed into a prompt that a wake interrupted, with its cursor position.
        self._carried = Document()
        # Jobs run on daemon threads, at most MAX_JOBS at once: they only post events and the chat is
        # saved as it goes, so one still waiting on a model call can be abandoned when the chat exits.
        self._slots = threading.Semaphore(MAX_JOBS)
        self._job_numbers = itertools.count(1)

    @property
    def prompting(self) -> bool:
        with self._lock:
            return self._active

    def width(self) -> int:
        """The terminal's width in cells (for fitting the toolbar)."""
        try:
            return self.session.app.output.get_size().columns
        except Exception:
            return 80

    def _render_toolbar(self) -> str:
        try:
            return self._toolbar()
        except Exception:  # a redraw must never take the prompt down
            return ""

    def chat_io(self, spawn: Spawn) -> ChatIO:
        return ChatIO(ask=self.ask, spawn=spawn, wake=self.wake, submit=self.submit)

    def ask(self, prompt: str) -> object:
        with self._lock:
            if self._wake_pending:
                self._wake_pending = False
                return WAKE
        default, self._carried = self._carried, Document()
        try:
            return self.session.prompt(self._message(prompt), default=default, pre_run=self._started)
        except EOFError:
            return None
        finally:
            with self._lock:
                self._active = False
            self.session.app.erase_when_done = False  # a submitted line stays in the scrollback

    def _message(self, prompt: str) -> Callable[[], FormattedText]:
        """The prompt's message, re-rendered on every redraw: the live row (if any) above the prompt.

        The final redraw of a finished prompt leaves the live row out, so a submitted line stays in
        the scrollback without a frozen copy of the row above it."""

        def message() -> FormattedText:
            try:
                row = self._live_row() if self._live_row and not self.session.app.is_done else ""
            except Exception:  # a redraw must never take the prompt down
                row = ""
            parts = [("class:live", row + "\n")] if row else []
            return FormattedText([*parts, ("bold", prompt)])

        return message

    def _started(self) -> None:
        # Runs on the prompt's event loop once the app is running (its future is set).
        with self._lock:
            self._active = True
            pending, self._wake_pending = self._wake_pending, False
        if pending:
            self._exit_with_wake()

    def _exit_with_wake(self) -> None:
        app = self.session.app
        if app.future is None or app.future.done():
            return  # the prompt already finished (the user pressed Enter first)
        self._carried = app.current_buffer.document  # text and cursor position
        app.erase_when_done = True  # the prompt comes straight back; leave no stale line behind
        app.exit(result=WAKE)

    def wake(self) -> None:
        with self._lock:
            loop = self.session.app.loop if self._active else None
            if loop is None:
                self._wake_pending = True
                return
        try:
            loop.call_soon_threadsafe(self._exit_with_wake)
        except RuntimeError:  # the loop closed as the prompt returned; wake the next one instead
            with self._lock:
                self._wake_pending = True

    def submit(self, job: Callable[[], None]) -> threading.Thread:
        """Start `job` on a daemon thread; it waits for a free slot there, so this never blocks."""

        def work() -> None:
            with self._slots:
                job()

        thread = threading.Thread(target=work, name=f"phil-chat-{next(self._job_numbers)}", daemon=True)
        thread.start()
        return thread

    def run(self, fn: Callable[[], Any]) -> Any:
        """Run the chat with stdout patched so output from the main thread prints above the prompt."""
        with patch_stdout(raw=True):
            return fn()

    def close(self) -> None:
        pass  # job threads are daemons: nothing to join, and exit never waits on a model call
