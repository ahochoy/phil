"""`ScreenIO`: the full-screen frame as the chat controller's `ChatIO`.

The Textual app runs on the main thread and the chat (the controller's loop) on a worker thread
named `phil-chat`. They meet in one result channel: a lock-protected queue of entered lines plus
flags (wake pending, interrupt held, frame closed) and the open menu's answer, under a condition the
chat thread waits on. The app thread only ever adds to it (`FrameInput` calls); the chat thread only
ever takes from it (`ask`, `choose`). Everything the chat changes on screen goes through `PhilApp`'s
thread-safe methods.
"""

import itertools
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import TypeVar

from phil.chat.controller import WAKE, ChatIO
from phil.chat.decision import Decision
from phil.chat.state import ToolbarView
from phil.ui.screen.app import PhilApp
from phil.ui.screen.console import PaneConsole

T = TypeVar("T")

# As `phil.chat.terminal` (not imported here: it loads prompt_toolkit and patches its key table).
MAX_JOBS = 3
Spawn = Callable[[Path, str, str, dict | None], object]

_EOF = object()  # Ctrl+D, queued in order with the lines entered before it
_NO_ANSWER = object()  # the open menu has no answer yet


class ScreenIO:
    """The full-screen frame's chat IO. Input is always live: lines entered while the chat is busy
    wait in order for the next `ask`."""

    def __init__(self, view: Callable[[], ToolbarView | None] = lambda: None) -> None:
        self._view = view
        self._ready = threading.Condition()
        self._lines: deque[object] = deque()
        self._wake_pending = False
        self._interrupt_held = False
        self._closed = False
        self._waiting = 0
        self._run_fn: Callable[[], object] | None = None  # `run`'s fn, started once the app mounts
        self._future: Future | None = None
        self._width = 80
        # The menu `choose` shows (None when none), its highlight and its answer; the highlight a
        # wake interrupted, kept for the next menu if it is of the same decision.
        self._menu: int | None = None
        self._menu_numbers = itertools.count(1)
        self._choice = 0
        self._answer: object = _NO_ANSWER
        self._carried_choice: tuple[Decision, int] | None = None
        # Jobs run on daemon threads, at most MAX_JOBS at once, as in `TerminalIO.submit`.
        self._slots = threading.Semaphore(MAX_JOBS)
        self._job_numbers = itertools.count(1)
        self.app = PhilApp(self)
        self.console = PaneConsole(self.app.post_item, self.width)

    # The chat's side (the worker thread).

    def chat_io(self, spawn: Spawn) -> ChatIO:
        # task_output once ScreenIO implements it.
        return ChatIO(ask=self.ask, spawn=spawn, wake=self.wake, submit=self.submit, choose=self.choose,
                      prefill=self.prefill, copy=self.copy, task_output=None)

    def ask(self, prompt: str) -> object:
        """The next entered line; `WAKE` if a wake is pending; None at EOF (Ctrl+D, or the frame
        closed); raises `KeyboardInterrupt` if Ctrl+C was pressed. Blocks until one of those. The
        prompt shows on the box's border; the box's text is never touched."""
        with self._ready:
            if not self._wake_pending:
                self._carried_choice = None  # a menu's highlight doesn't survive a typed prompt
        self.app.set_prompt(prompt)
        return self._next()

    def choose(self, prompt: str, decision: Decision) -> object:
        """Dock `decision`'s callout above the input box and answer it from the keyboard (the keys
        are `DecisionMenu`'s). Returns the picked option's answer, `TYPE` (Esc), `WAKE` (keeping
        the highlight for the next menu of the same decision), or None (Ctrl+D, or the frame
        closed); raises `KeyboardInterrupt` for Ctrl+C. Lines typed before it stay queued for the
        next `ask`. `prompt` isn't shown: the callout's title says what is being asked."""
        n = len(decision.options)
        with self._ready:
            if self._interrupt_held:
                self._interrupt_held = False
                raise KeyboardInterrupt
            if self._wake_pending:
                self._wake_pending = False
                return WAKE
            if self._closed:
                return None
            carried, self._carried_choice = self._carried_choice, None
            start = decision.default if 0 <= decision.default < n else 0
            if carried is not None and carried[0] == decision and 0 <= carried[1] < n:
                start = carried[1]
            menu = self._menu = next(self._menu_numbers)
            self._choice = start
            self._answer = _NO_ANSWER
        self.app.open_menu(menu, decision, start)
        try:
            with self._ready:
                while True:
                    if self._interrupt_held:
                        self._interrupt_held = False
                        raise KeyboardInterrupt
                    if self._wake_pending:
                        self._wake_pending = False
                        self._carried_choice = (decision, self._choice)
                        return WAKE
                    if self._closed:
                        return None
                    if self._answer is not _NO_ANSWER:
                        return self._answer
                    self._ready.wait()
        finally:
            with self._ready:
                self._menu = None
            self.app.close_menu(menu)

    def _next(self) -> object:
        with self._ready:
            self._waiting += 1
            try:
                while True:
                    if self._interrupt_held:
                        self._interrupt_held = False
                        raise KeyboardInterrupt
                    if self._wake_pending:
                        self._wake_pending = False
                        return WAKE
                    if self._closed:  # nothing typed is acted on once the frame is gone
                        return None
                    if self._lines:
                        line = self._lines.popleft()
                        return None if line is _EOF else line
                    self._ready.wait()
            finally:
                self._waiting -= 1

    @property
    def asking(self) -> bool:
        """The chat is waiting in `ask` for input."""
        with self._ready:
            return self._waiting > 0

    def prefill(self, text: str) -> None:
        """Put `text` in the box, the cursor at its end, to send or edit."""
        self.app.prefill(text)

    def wake(self) -> None:
        """Any thread: a blocked `ask` returns `WAKE`; with none blocked, the next one does."""
        with self._ready:
            self._wake_pending = True
            self._ready.notify_all()

    def submit(self, job: Callable[[], None]) -> threading.Thread:
        """Start `job` on a daemon thread; it waits for a free slot there, so this never blocks."""

        def work() -> None:
            with self._slots:
                job()

        thread = threading.Thread(target=work, name=f"phil-job-{next(self._job_numbers)}", daemon=True)
        thread.start()
        return thread

    def copy(self, text: str) -> bool:
        """Put `text` on the clipboard; True when the frame is running."""
        return self.app.copy_text(text)

    def width(self) -> int:
        """The conversation pane's content width (80 before the frame is laid out; the last known
        width after it closes)."""
        width = self.app.pane_width()
        if width:
            self._width = width
        return self._width

    def set_view(self, view: Callable[[], ToolbarView | None]) -> None:
        self._view = view

    # The frame's side (`FrameInput`, called on the app's thread).

    def frame_mounted(self) -> None:
        if self._run_fn is not None and self._future is None:
            self._future = self.start(self._run_fn)

    def line_entered(self, text: str) -> None:
        self._put(text)

    def end_of_input(self) -> None:
        self._put(_EOF)

    def interrupt(self) -> None:
        """Ctrl+C with no selection: raised at once from a blocked `ask`, else held for the next.
        Lines typed ahead are dropped (as the inline prompt drops them), so nothing the user just
        cancelled runs after the interrupt; a queued EOF stays. The box's text is untouched."""
        with self._ready:
            eof = _EOF in self._lines
            self._lines.clear()
            if eof:
                self._lines.append(_EOF)
            self._interrupt_held = True
            self._ready.notify_all()

    def frame_closed(self) -> None:
        with self._ready:
            self._closed = True
            self._ready.notify_all()

    def choice_moved(self, menu_id: int, index: int) -> None:
        with self._ready:
            if menu_id == self._menu:
                self._choice = index

    def choice_made(self, menu_id: int, answer: object) -> None:
        """An answer for menu `menu_id`; ignored if that menu has closed (a wake got there first)."""
        with self._ready:
            if menu_id == self._menu and self._answer is _NO_ANSWER:
                self._answer = answer
                self._ready.notify_all()

    def _put(self, line: object) -> None:
        with self._ready:
            self._lines.append(line)
            self._ready.notify_all()

    # Running.

    def start(self, fn: Callable[[], T]) -> "Future[T]":
        """Run `fn` on a daemon worker thread named `phil-chat`; when it returns or raises, a held
        interrupt is dropped and the app exits. The future holds `fn`'s value or exception."""
        future: Future[T] = Future()

        def work() -> None:
            try:
                result = fn()
            except BaseException as exc:  # KeyboardInterrupt too: `run` re-raises it after exit
                outcome: tuple[bool, object] = (False, exc)
            else:
                outcome = (True, result)
            with self._ready:
                self._interrupt_held = False  # a Ctrl+C no ask took must not outlive the chat
            self.app.request_exit()
            if outcome[0]:
                future.set_result(outcome[1])  # type: ignore[arg-type]
            else:
                future.set_exception(outcome[1])  # type: ignore[arg-type]

        threading.Thread(target=work, name="phil-chat", daemon=True).start()
        return future

    def run(self, fn: Callable[[], T]) -> T:
        """Run the app on this thread and `fn` on a worker started once the app mounts; return
        `fn`'s value, or re-raise its exception, after the app has exited (terminal restored).
        If the app itself failed (a handler raised), raise `RuntimeError` chained to that failure
        instead: the chat saw EOF, which is not a clean exit."""
        self._run_fn = fn
        try:
            self.app.run()
        finally:
            with self._ready:
                self._interrupt_held = False
        if self._future is None:
            raise RuntimeError("the full-screen frame closed before the chat started")
        try:
            result = self._future.result()  # the chat finishes: a closed frame answers EOF
        finally:
            if self.app.return_code:
                cause = getattr(self.app, "_exception", None)  # Textual keeps the first failure
                raise RuntimeError("the full-screen chat stopped unexpectedly") from cause
        return result

    def close(self) -> None:
        self.app.request_exit()
