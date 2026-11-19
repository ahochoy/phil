"""`PhilApp`: the full-screen chat's Textual app — composition, CSS, bindings and the
thread-safe entry points the chat (running on another thread) calls."""

import asyncio
import threading
from collections.abc import Callable
from typing import ClassVar, Literal

from rich.console import RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType

from phil.ui.screen.widgets import (
    Conversation,
    DecisionMenu,
    HeaderBar,
    Item,
    LiveLine,
    NewMarker,
    PromptBox,
)

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


class PhilApp(App, inherit_bindings=False):
    """The full-screen frame. `post_item`, `set_header` and `clear_conversation` may be called from
    any thread; before the app mounts they are kept and applied on mount, and once it has exited
    they do nothing."""

    CSS = CSS
    ENABLE_COMMAND_PALETTE = False
    ALLOW_SELECT = True
    # Not inherited: Textual's default Ctrl+Q (quit) and Ctrl+C (quit hint) bindings stay off.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("pageup", "scroll_conversation('page_up')", show=False, priority=True),
        Binding("pagedown", "scroll_conversation('page_down')", show=False, priority=True),
        Binding("shift+up", "scroll_conversation('up')", show=False, priority=True),
        Binding("shift+down", "scroll_conversation('down')", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._phase: Literal["pending", "running", "closed"] = "pending"
        self._pending: list[Callable[[], None]] = []
        self._entry_loop: asyncio.AbstractEventLoop | None = None  # not `_loop`: App has its own
        self._app_thread: int | None = None

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
            self._app_thread = threading.get_ident()
            self._phase = "running"
            pending, self._pending = self._pending, []
        for apply in pending:
            apply()

    def on_unmount(self) -> None:
        self._close()

    def exit(self, *args, **kwargs) -> None:
        self._close()
        super().exit(*args, **kwargs)

    # Thread-safe entry points.

    def post_item(self, renderable: RenderableType) -> None:
        self._call(lambda: self.query_one(Conversation).add(Item(renderable)))

    def set_header(self, repo: str, branch: str, chat_id: str) -> None:
        self._call(lambda: self.query_one(HeaderBar).set_header(repo, branch, chat_id))

    def clear_conversation(self) -> None:
        self._call(lambda: self.query_one(Conversation).clear())

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

    def _call(self, apply: Callable[[], None]) -> None:
        """Run `apply` on the app's thread: now if called there, else soon; keep it until mount;
        drop it once the app has exited. Never raises for a closed app."""
        with self._lock:
            if self._phase == "pending":
                self._pending.append(apply)
                return
            if self._phase == "closed":
                return
            loop, app_thread = self._entry_loop, self._app_thread
        if threading.get_ident() == app_thread:
            apply()
            return
        try:
            loop.call_soon_threadsafe(self._apply_if_running, apply)  # type: ignore[union-attr]
        except RuntimeError:  # the loop closed between the check and the call
            pass

    def _apply_if_running(self, apply: Callable[[], None]) -> None:
        if self._phase == "running":
            apply()
