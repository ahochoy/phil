"""`PhilApp`: the full-screen chat's Textual app — composition, CSS, bindings and the
thread-safe entry points the chat (running on another thread) calls."""

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import ClassVar, Literal, Protocol

from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import TextArea
from textual.widgets.text_area import Selection

from phil.chat.decision import Decision
from phil.chat.state import ToolbarView
from phil.ui.screen import clipboard
from phil.ui.screen.widgets import (
    Conversation,
    DecisionMenu,
    HeaderBar,
    Item,
    LiveLine,
    NewMarker,
    PromptBox,
    TaskBlock,
)

_log = logging.getLogger(__name__)

LIVE_REFRESH = 0.5  # seconds between live-line refreshes

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
    def choice_moved(self, menu_id: int, index: int) -> None: ...
    def choice_made(self, menu_id: int, answer: object) -> None: ...


class PhilApp(App, inherit_bindings=False):
    """The full-screen frame. `post_item`, the task output (`task_started`, `task_line`,
    `sub_agent`, `task_done`), `set_header`, `clear_conversation`, `set_prompt`, `prefill`,
    `open_menu`, `close_menu`, `copy_text` and `request_exit` may be called from any
    thread; before the app mounts they are kept and applied on mount, and once it has exited they do
    nothing."""

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

    def __init__(
        self, frame_input: FrameInput | None = None, view: Callable[[], ToolbarView | None] = lambda: None
    ) -> None:
        super().__init__()
        self._input = frame_input
        self._view = view  # what the live line shows, read on the app's thread every LIVE_REFRESH
        self._lock = threading.Lock()
        self._phase: Literal["pending", "running", "closed"] = "pending"
        self._pending: list[Callable[[], None]] = []
        self._entry_loop: asyncio.AbstractEventLoop | None = None  # not `_loop`: App has its own
        self._pane_width: int | None = None  # set on the app's thread as the conversation resizes
        self._tasks: dict[str, TaskBlock] = {}  # each task id's latest block (app thread only)

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        yield Conversation(id="conversation")
        yield NewMarker(id="new")
        yield LiveLine(self._view, id="live")
        yield DecisionMenu(id="menu")
        yield PromptBox(id="prompt")

    def on_mount(self) -> None:
        self.query_one(PromptBox).focus()
        live = self.query_one(LiveLine)
        live.tick()
        self.set_interval(LIVE_REFRESH, live.tick)  # the live line only, not the conversation
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

    def on_descendant_focus(self, event) -> None:
        """While a menu shows it keeps the keyboard: a click elsewhere (a drag to select text in
        the conversation, say) hands focus straight back; the selection is the screen's and stays."""
        menu = self.query_one(DecisionMenu)
        if menu.decision is not None and event.widget is not menu:
            menu.focus(scroll_visible=False)

    def on_decision_menu_moved(self, message: DecisionMenu.Moved) -> None:
        if self._input is not None:
            self._input.choice_moved(message.menu_id, message.index)

    def on_decision_menu_chosen(self, message: DecisionMenu.Chosen) -> None:
        if self._input is not None:
            self._input.choice_made(message.menu_id, message.answer)

    def action_copy_or_interrupt(self) -> None:
        """Ctrl+C: copy the selection (in the conversation, else in the box) if there is one, then
        clear it, so the next Ctrl+C interrupts; otherwise interrupt."""
        text = self.screen.get_selected_text()
        if text:
            self.screen.clear_selection()
        elif isinstance(self.focused, TextArea) and self.focused.selected_text:
            box = self.focused
            text = box.selected_text
            box.selection = Selection.cursor(box.selection.end)
        if text:
            self.copy_to_clipboard(text)
            # The clipboard command may take a moment: not on the app's thread.
            threading.Thread(target=clipboard.system_copy, args=(text,), name="phil-copy", daemon=True).start()
            self.notify("Copied", timeout=1.5)
        elif self._input is not None:
            self._input.interrupt()

    # Thread-safe entry points.

    def post_item(self, renderable: RenderableType) -> None:
        self._call(lambda: self.query_one(Conversation).add(Item(renderable)))

    def set_header(self, repo: str, branch: str, chat_id: str) -> None:
        self._call(lambda: self.query_one(HeaderBar).set_header(repo, branch, chat_id))

    def clear_conversation(self) -> None:
        """Empty the conversation; tasks after it start new blocks."""

        def apply() -> None:
            self._tasks.clear()
            self.query_one(Conversation).clear()

        self._call(apply)

    def set_prompt(self, label: str) -> None:
        """Show what is being asked on the input box's border (never as text in the box)."""
        self._call(lambda: setattr(self.query_one(PromptBox), "border_title", label.strip()))

    def prefill(self, text: str) -> None:
        """Replace the input box's text with `text`, the cursor at its end."""
        self._call(lambda: self.query_one(PromptBox).set_text(text))

    def open_menu(self, menu_id: int, decision: Decision, highlighted: int) -> None:
        """Dock `decision`'s callout above the input box with the keys; the box keeps its text but
        takes no keys (nor focus) and the live line collapses to one row until `close_menu`."""

        def apply() -> None:
            self.query_one(PromptBox).can_focus = False
            self.query_one(LiveLine).set_max_lines(1)
            self.query_one(DecisionMenu).show(menu_id, decision, highlighted)

        self._call(apply)

    def close_menu(self, menu_id: int) -> None:
        """Hide menu `menu_id` (if it is still the one showing) and give the box its keys back."""

        def apply() -> None:
            menu = self.query_one(DecisionMenu)
            if menu.menu_id != menu_id:
                return
            menu.hide()
            self.query_one(LiveLine).set_max_lines(None)
            box = self.query_one(PromptBox)
            box.can_focus = True
            box.focus()

        self._call(apply)

    def copy_text(self, text: str) -> Literal["clipboard", "terminal"] | None:
        """Put `text` on the clipboard: with the system's clipboard command ("clipboard" when one
        succeeded; it runs on the calling thread) and through the terminal (OSC 52) as well, which
        is all there is otherwise ("terminal"). None (and nothing is sent) unless the app is running."""
        with self._lock:
            if self._phase != "running":
                return None
        copied = clipboard.system_copy(text)
        self._call(lambda: self.copy_to_clipboard(text))
        return "clipboard" if copied else "terminal"

    def request_exit(self) -> None:
        self._call(self.exit)

    def task_started(self, task: str, band: RenderableType) -> None:
        """A new block for `task`, headed by `band`, at the end of the conversation (always new: a
        later run reusing the id starts its own block; earlier ones stay as they are)."""

        def apply() -> None:
            block = self._tasks[task] = TaskBlock(band)
            self.query_one(Conversation).add(block)

        self._call(apply)

    def task_line(self, task: str, line: RenderableType) -> None:
        """`line` at the end of `task`'s block (opening it again if it was folded)."""
        self._call(lambda: self._add_task_line(task, line))

    def sub_agent(self, task: str, seq: int, description: str) -> None:
        """`└ sub-agent  <description>` in `task`'s block. The controller announces each sub-agent
        once per run, so every call shows."""
        self._call(lambda: self._add_task_line(task, Text(f"└ sub-agent  {description}", style="phil.sub")))

    def task_done(self, task: str, folded: RenderableType) -> None:
        """Fold `task`'s block to the one line `folded`."""
        self._call(lambda: self._block(task).fold(folded))

    def _block(self, task: str) -> TaskBlock:
        """`task`'s block; a task never announced gets one, headed by its id."""
        block = self._tasks.get(task)
        if block is None:
            block = self._tasks[task] = TaskBlock(Text(task))
            self.query_one(Conversation).add(block)
        return block

    def _add_task_line(self, task: str, line: RenderableType) -> None:
        self.query_one(Conversation).add(Item(line), into=self._block(task))

    def note_pane_width(self, width: int) -> None:
        """The conversation's content width after a resize (the app's thread)."""
        with self._lock:
            self._pane_width = width or None

    def pane_width(self) -> int | None:
        """The conversation pane's content width (what items render at); None before layout and
        once closed. Safe from any thread: it returns the width the app's thread last noted."""
        with self._lock:
            return self._pane_width if self._phase == "running" else None

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
