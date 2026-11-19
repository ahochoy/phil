"""`PhilApp`: the full-screen chat's Textual app — composition, CSS, bindings and the
thread-safe entry points the chat (running on another thread) calls."""

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import ClassVar, Literal, Protocol

from rich.console import RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import TextArea

from phil.ui.screen.widgets import (
    Conversation,
    DecisionMenu,
    HeaderBar,
    Item,
    LiveLine,
    NewMarker,
    PromptBox,
)

_log = logging.getLogger(__name__)

CSS = """
Screen {
    layout: vertical;
}
#header {
    height: 1;
    padding: 0 1;
}
#conversation {
    height: 1fr;
    padding: 0 1;
}
#new {
    height: 1;
    padding: 0 1;
    color: ansi_cyan;
    text-style: dim;
    display: none;
}
#live {
    height: auto;
    padding: 0 1;
    display: none;
}
#menu {
    height: auto;
    display: none;
}
#prompt {
    height: auto;
    max-height: 8;
    border: round ansi_bright_black;
}
#prompt:focus {
    border: round ansi_cyan;
}
"""


class FrameInput(Protocol):
    """Where the frame sends what the user does (`ScreenIO`). Called on the app's thread; each
    call must return at once."""

    def frame_mounted(self) -> None: ...
    def line_entered(self, text: str) -> None: ...
    def end_of_input(self) -> None: ...
    def interrupt(self) -> None: ...
    def frame_closed(self) -> None: ...


class PhilApp(App, inherit_bindings=False):
    """The full-screen frame. `post_item`, `set_header`, `clear_conversation`, `set_prompt`,
    `prefill`, `copy_text` and `request_exit` may be called from any thread; before the app mounts
    they are kept and applied on mount, and once it has exited they do nothing."""

    CSS = CSS
    ENABLE_COMMAND_PALETTE = False
    ALLOW_SELECT = True
    # Not inherited: Textual's default Ctrl+Q (quit) and Ctrl+C (quit hint) bindings stay off.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("pageup", "scroll_conversation('page_up')", show=False, priority=True),
        Binding("pagedown", "scroll_conversation('page_down')", show=False, priority=True),
        Binding("shift+up", "scroll_conversation('up')", show=False, priority=True),
        Binding("shift+down", "scroll_conversation('down')", show=False, priority=True),
        Binding("ctrl+c", "copy_or_interrupt", show=False, priority=True),
    ]

    def __init__(self, frame_input: FrameInput | None = None) -> None:
        super().__init__()
        self._input = frame_input
        self._lock = threading.Lock()
        self._phase: Literal["pending", "running", "closed"] = "pending"
        self._pending: list[Callable[[], None]] = []
        self._entry_loop: asyncio.AbstractEventLoop | None = None  # not `_loop`: App has its own

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        yield Conversation(id="conversation")
        yield NewMarker(id="new")
        yield LiveLine(id="live")
        yield DecisionMenu(id="menu")
        yield PromptBox(id="prompt")

    def on_mount(self) -> None:
        self.query_one(PromptBox).focus()
        with self._lock:
            self._entry_loop = asyncio.get_running_loop()
            self._phase = "running"
            pending, self._pending = self._pending, []
        for apply in pending:
            self._apply_if_running(apply)
        if self._input is not None:
            self._input.frame_mounted()

    def on_unmount(self) -> None:
        self._close()

    def exit(self, *args, **kwargs) -> None:
        self._close()
        super().exit(*args, **kwargs)

    # What the user does, handed to the frame's input.

    def on_prompt_box_submitted(self, message: PromptBox.Submitted) -> None:
        if self._input is not None:
            self._input.line_entered(message.text)

    def on_prompt_box_end_of_input(self, message: PromptBox.EndOfInput) -> None:
        if self._input is not None:
            self._input.end_of_input()

    def action_copy_or_interrupt(self) -> None:
        """Ctrl+C: copy the selection (in the conversation, else in the box) if there is one;
        otherwise interrupt."""
        text = self.screen.get_selected_text()
        if not text and isinstance(self.focused, TextArea):
            text = self.focused.selected_text
        if text:
            self.copy_to_clipboard(text)
        elif self._input is not None:
            self._input.interrupt()

    # Thread-safe entry points.

    def post_item(self, renderable: RenderableType) -> None:
        self._call(lambda: self.query_one(Conversation).add(Item(renderable)))

    def set_header(self, repo: str, branch: str, chat_id: str) -> None:
        self._call(lambda: self.query_one(HeaderBar).set_header(repo, branch, chat_id))

    def clear_conversation(self) -> None:
        self._call(lambda: self.query_one(Conversation).clear())

    def set_prompt(self, label: str) -> None:
        """Show what is being asked on the input box's border (never as text in the box)."""
        self._call(lambda: setattr(self.query_one(PromptBox), "border_title", label.strip()))

    def prefill(self, text: str) -> None:
        """Replace the input box's text with `text`, the cursor at its end."""
        self._call(lambda: self.query_one(PromptBox).set_text(text))

    def copy_text(self, text: str) -> bool:
        """Put `text` on the clipboard; False (and nothing is kept) unless the app is running."""
        with self._lock:
            if self._phase != "running":
                return False
        self._call(lambda: self.copy_to_clipboard(text))
        return True

    def request_exit(self) -> None:
        self._call(self.exit)

    def pane_width(self) -> int | None:
        """The conversation pane's content width (what items render at); None before layout.
        Safe from any thread: it only reads sizes."""
        with self._lock:
            if self._phase != "running":
                return None
        try:
            width = self.query_one(Conversation).scrollable_content_region.width
        except Exception:  # the tree is changing (shutdown): no width to report
            return None
        return width or None

    def action_scroll_conversation(self, how: str) -> None:
        conversation = self.query_one(Conversation)
        {
            "page_up": conversation.scroll_page_up,
            "page_down": conversation.scroll_page_down,
            "up": conversation.scroll_up,
            "down": conversation.scroll_down,
        }[how](animate=False)

    def _close(self) -> None:
        with self._lock:
            self._phase = "closed"
            self._pending = []
        if self._input is not None:
            self._input.frame_closed()

    def _call(self, apply: Callable[[], None]) -> None:
        """Run `apply` on the app's thread, in call order whichever thread calls; keep it until
        mount; drop it once the app has exited. Never raises, and never blocks."""
        with self._lock:
            if self._phase == "pending":
                self._pending.append(apply)
                return
            if self._phase == "closed":
                return
            loop = self._entry_loop
        try:
            # Through the loop even from the app's own thread, so a call can't overtake calls
            # other threads queued before it.
            loop.call_soon_threadsafe(self._apply_if_running, apply)  # type: ignore[union-attr]
        except RuntimeError:  # the loop closed between the check and the call
            pass

    def _apply_if_running(self, apply: Callable[[], None]) -> None:
        """Run `apply` in Textual's context (the loop callback carries the caller's contextvars,
        not the app's); a failure is logged, never printed over the frame."""
        if self._phase != "running":
            return
        with self._context():
            try:
                apply()
            except Exception:
                _log.exception("a frame update failed")
