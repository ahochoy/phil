"""The full-screen frame's widgets (the tree is in `app.py`'s `PhilApp.compose`)."""

import logging
import time
from collections.abc import Callable

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual import events
from textual.containers import Vertical, VerticalScroll
from textual.geometry import Size
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static, TextArea

from phil.chat.controller import TYPE
from phil.chat.decision import Decision
from phil.chat.state import ToolbarView
from phil.ui.callout import callout_lines, to_rich
from phil.ui.fragments import to_text
from phil.ui.screen.themed import Themed
from phil.ui.toolbar import render_frame_live

_log = logging.getLogger(__name__)


class HeaderBar(Static):
    """One line: `◆ phil · <repo> · <branch>` on the left, `chat <id>` on the right."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._header: tuple[str, str, str] | None = None

    def set_header(self, repo: str, branch: str, chat_id: str) -> None:
        self._header = (repo, branch, chat_id)
        self.refresh()

    def render(self) -> RenderableType:
        if self._header is None:
            return Themed(Text("◆ phil", style="phil.brand"))
        repo, branch, chat_id = self._header
        left = Text.assemble(("◆ phil", "phil.brand"), (f" · {repo} · {branch}", "phil.muted"))
        right = Text(f"chat {chat_id}", style="phil.muted")
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        grid.add_column(no_wrap=True, justify="right")
        grid.add_row(left, right)
        return Themed(grid)


class Item(Widget):
    """One printed renderable in the conversation, shown as selectable text.

    Textual selects text only in `Text`/`Content` visuals, so the renderable (a Text, a Group, a
    table, a callout...) is rendered under `PHIL_THEME` at the pane's width into `Text` with the
    styles resolved to colours, and rendered again when the width changes."""

    DEFAULT_CSS = """
    Item {
        height: auto;
    }
    """

    def __init__(self, renderable: RenderableType) -> None:
        super().__init__()
        self.renderable = renderable
        self._styled: tuple[int, Text, int] | None = None  # (width, styled text, its height)

    def set_renderable(self, renderable: RenderableType) -> None:
        """Show `renderable` instead (a task block's header when it folds or opens again)."""
        self.renderable = renderable
        self._styled = None
        self.refresh(layout=True)

    def _render_at(self, width: int) -> tuple[int, Text, int]:
        if self._styled is None or self._styled[0] != width:
            console = self.app.console
            lines = console.render_lines(Themed(self.renderable), console.options.update_width(width), pad=False)
            text = Text(no_wrap=True, overflow="crop", end="")
            for n, line in enumerate(lines):
                if n:
                    text.append("\n")
                for segment in line:
                    if not segment.control:
                        text.append(segment.text, segment.style)
            self._styled = (width, text, max(len(lines), 1))
        return self._styled

    def styled(self, width: int) -> Text:
        """The renderable as styled text, `width` cells wide at most (cached for the last width)."""
        return self._render_at(width)[1]

    def render(self) -> Text:
        return self.styled(max(self.size.width, 1))

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        if not width:
            return 0
        return self._render_at(width)[2]


class TaskBlock(Vertical):
    """A task's band (`header`) and its feed lines below it, each an `Item`. `fold` removes the
    lines and shows the folded band instead; a line added to a folded block opens it again, under
    the original band. Lines are added through `Conversation.add`, so they follow like any item."""

    DEFAULT_CSS = """
    TaskBlock {
        height: auto;
    }
    """

    def __init__(self, band: RenderableType) -> None:
        self.band = band
        self.header = Item(band)
        self.folded = False
        self._early: list[Item] = []  # lines added before the block (and its header) mounted
        super().__init__(self.header)

    def on_mount(self) -> None:
        early, self._early = self._early, []
        if early:
            self.mount(*early)

    @property
    def lines(self) -> list[Item]:
        mounted = [child for child in self.children if isinstance(child, Item) and child is not self.header]
        return mounted + self._early

    def add_line(self, item: Item) -> None:
        """`item` below the block's lines, opening the block if it is folded."""
        if self.folded:
            self.folded = False
            self.header.set_renderable(self.band)
        if self.is_mounted:
            self.mount(item)
        else:  # the header mounts with the block: a line mounted now would land above it
            self._early.append(item)

    def fold(self, folded: RenderableType) -> None:
        self.folded = True
        self._early = []
        for line in self.lines:
            line.remove()
        self.header.set_renderable(folded)


class NewMarker(Static):
    """`↓ new`: shown while the conversation is scrolled up and output arrived below the view."""

    def __init__(self, **kwargs) -> None:
        super().__init__("↓ new", **kwargs)


class Conversation(VerticalScroll, can_focus=False):
    """The scrolling conversation: follows new output while at the bottom; when the user has
    scrolled up, new output shows the `NewMarker` and the view stays where it is. It never takes
    focus (a click, a drag or Tab leaves the keys with the input box): the scroll keys are the
    app's bindings, and a drag's selection is the screen's."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.following = True

    def add(self, widget: Item | TaskBlock, into: TaskBlock | None = None) -> None:
        """Add `widget` at the end, or at the end of the task block `into` (opening it if folded)."""
        if into is None:
            self.mount(widget)
        else:
            into.add_line(widget)
        if self.following:
            self.scroll_end(animate=False)
        else:
            self._marker().display = True

    def clear(self) -> None:
        self.remove_children()
        self.to_bottom()

    def to_bottom(self) -> None:
        """Return to the bottom and resume following."""
        self.following = True
        self._marker().display = False
        self.scroll_end(animate=False)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if new_value >= self.max_scroll_y:
            self.following = True
            self._marker().display = False
        elif new_value < old_value:
            self.following = False

    def watch_virtual_size(self, old: Size, new: Size) -> None:
        """Content grew after the add's own scroll (a task block's line lays out a pass later)."""
        if self.following and new.height > old.height:
            self.scroll_end(animate=False)

    # The pane's content width, noted for other threads whenever it can change.
    def on_resize(self, event: events.Resize) -> None:
        self.app.note_pane_width(self.scrollable_content_region.width)

    def watch_show_vertical_scrollbar(self, show: bool) -> None:
        self.app.note_pane_width(self.scrollable_content_region.width)

    def _marker(self) -> NewMarker:
        return self.screen.query_one(NewMarker)


class LiveLine(Widget):
    """The live line under the conversation: `render_frame_live` of the view, each left row a line
    and the right side (time, cost) right-aligned on the first; hidden when there is nothing live.
    `tick` takes a fresh view (the app calls it every 0.5 s); the rows are laid out at the widget's
    own width. `max_lines` caps its rows (1 while a decision menu shows; None for no cap)."""

    DEFAULT_CSS = """
    LiveLine {
        height: auto;
    }
    """

    def __init__(self, view: Callable[[], ToolbarView | None] = lambda: None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._view = view
        self.max_lines: int | None = None
        self._snapshot: tuple[ToolbarView, float] | None = None
        self._lines: tuple[int, list[Text]] | None = None  # (width, lines) for the snapshot

    def set_max_lines(self, max_lines: int | None) -> None:
        self.max_lines = max_lines
        self.tick()

    def tick(self) -> None:
        """Take a fresh view and redraw; a re-layout only when the row count (or showing) changes.
        A view or render that fails is logged and the last good rows stay (it runs on a timer, where
        an exception would end the app)."""
        before = (self._snapshot, self._lines)
        shown = len(self._lines[1]) if self.display and self._lines is not None else 0
        try:
            view = self._view()
            self._snapshot = None if view is None else (view, time.time())
            self._lines = None
            # Hidden, it has no width: lay out at the screen's, less the padding, to see if it shows.
            width = self.content_size.width or self.app.size.width - self.styles.gutter.width
            rows = len(self._rows(max(width, 1)))
        except Exception:
            _log.exception("the live line's view failed")
            self._snapshot, self._lines = before
            return
        self.display = rows > 0
        self.refresh(layout=rows != shown)

    def _rows(self, width: int) -> list[Text]:
        if self._snapshot is None:
            return []
        if self._lines is None or self._lines[0] != width:
            view, now = self._snapshot
            live = render_frame_live(view, now, width, self.max_lines)
            rows = [to_text(row) for row in live.left]
            if rows:
                right = to_text(live.right)
                gap = max(width - rows[0].cell_len - right.cell_len, 1 if right else 0)
                rows[0] = Text.assemble(rows[0], " " * gap, right)
            self._lines = (width, rows)
        return self._lines[1]

    def render(self) -> RenderableType:
        text = Text("\n").join(self._rows(max(self.content_size.width, 1)))
        text.no_wrap, text.overflow, text.end = True, "crop", ""
        return Themed(text)

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._rows(width)) if width else 0


class DecisionMenu(Widget, can_focus=True):
    """A docked decision callout; hidden unless a choice is being asked. While it shows it has the
    keys: ↑/↓ move the highlight (wrapping), Enter picks it, 1-9 pick at once, Esc is `TYPE`,
    Ctrl+D is None (EOF); every other key is ignored. Each answer is `Chosen`, once per menu."""

    DEFAULT_CSS = """
    DecisionMenu {
        height: auto;
    }
    """

    class Moved(Message):
        def __init__(self, menu_id: int, index: int) -> None:
            super().__init__()
            self.menu_id = menu_id
            self.index = index

    class Chosen(Message):
        def __init__(self, menu_id: int, answer: object) -> None:
            super().__init__()
            self.menu_id = menu_id
            self.answer = answer

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.menu_id = 0
        self.decision: Decision | None = None
        self.highlighted = 0
        self._answered = False

    def show(self, menu_id: int, decision: Decision, highlighted: int) -> None:
        self.menu_id = menu_id
        self.decision = decision
        self.highlighted = highlighted
        self._answered = False
        self.display = True
        self.refresh(layout=True)
        self.focus()

    def hide(self) -> None:
        self.decision = None
        self.display = False

    def _lines(self, width: int) -> list:
        if self.decision is None or width < 1:
            return []
        return callout_lines(self.decision, self.highlighted, width, live=True)

    def render(self) -> RenderableType:
        return Themed(to_rich(self._lines(self.size.width)))

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._lines(width))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if self.decision is None or self._answered:
            return
        options = self.decision.options
        key = event.key
        if key in ("up", "down") and options:
            self.highlighted = (self.highlighted + (1 if key == "down" else -1)) % len(options)
            self.refresh(layout=True)
            self.post_message(self.Moved(self.menu_id, self.highlighted))
        elif key == "enter" and 0 <= self.highlighted < len(options):
            self._choose(options[self.highlighted].answer)
        elif key == "escape":
            self._choose(TYPE)
        elif key == "ctrl+d":
            self._choose(None)
        elif len(key) == 1 and key in "123456789" and int(key) <= len(options):
            self._choose(options[int(key) - 1].answer)

    def _choose(self, answer: object) -> None:
        self._answered = True
        self.post_message(self.Chosen(self.menu_id, answer))


class PromptBox(TextArea):
    """The input box, always live. Enter sends its text (`Submitted`) and clears it; Alt+Enter,
    Shift+Enter and Ctrl+Enter insert a newline; Ctrl+D in an empty box is `EndOfInput`; a paste
    inserts as text. Any key typed here returns the conversation to the bottom."""

    NEWLINE_KEYS = frozenset({"alt+enter", "shift+enter", "ctrl+enter"})

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class EndOfInput(Message):
        pass

    # A public `on_key`, not an `_on_key` override calling super: Textual dispatches each class's
    # handler along the MRO, so this runs first and TextArea's `_on_key` runs once after it (not at
    # all for a key handled here, which prevents the default).
    def on_key(self, event: events.Key) -> None:
        self.screen.query_one(Conversation).to_bottom()
        if event.key == "enter":
            self._handled(event)
            text = self.text
            self.clear()
            self.post_message(self.Submitted(text))
        elif event.key in self.NEWLINE_KEYS:
            self._handled(event)
            start, end = self.selection
            self.replace("\n", start, end, maintain_selection_offset=False)
        elif event.key == "ctrl+d" and not self.text:
            self._handled(event)
            self.post_message(self.EndOfInput())

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if not self.can_focus:  # a decision menu has the keys: a click leaves the cursor alone
            event.stop()
            event.prevent_default()

    def set_text(self, text: str) -> None:
        """Replace the box's text, the cursor at its end."""
        self.text = text
        self.move_cursor(self.document.end)

    @staticmethod
    def _handled(event: events.Key) -> None:
        event.stop()
        event.prevent_default()
