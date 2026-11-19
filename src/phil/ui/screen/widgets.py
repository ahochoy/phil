"""The full-screen frame's widgets (the tree is in `app.py`'s `PhilApp.compose`)."""

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
from phil.ui.callout import callout_lines, to_rich
from phil.ui.screen.themed import Themed


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
        self._styled: tuple[int, Text] | None = None

    def styled(self, width: int) -> Text:
        """The renderable as styled text, `width` cells wide at most (cached for the last width)."""
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
            self._styled = (width, text)
        return self._styled[1]

    def render(self) -> Text:
        return self.styled(max(self.size.width, 1))

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        if not width:
            return 0
        return len(self.styled(width).split("\n"))


class TaskBlock(Vertical):
    """A task's band and its feed lines, foldable (behaviour comes with the task output)."""


class NewMarker(Static):
    """`↓ new`: shown while the conversation is scrolled up and output arrived below the view."""

    def __init__(self, **kwargs) -> None:
        super().__init__("↓ new", **kwargs)


class Conversation(VerticalScroll):
    """The scrolling conversation: follows new output while at the bottom; when the user has
    scrolled up, new output shows the `NewMarker` and the view stays where it is."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.following = True

    def add(self, widget: Item | TaskBlock) -> None:
        self.mount(widget)
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

    def _marker(self) -> NewMarker:
        return self.screen.query_one(NewMarker)


class LiveLine(Static):
    """The live line under the conversation (content comes with the live status); hidden if empty.
    `max_lines` caps its rows (1 while a decision menu shows; None for no cap)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.max_lines: int | None = None

    def set_max_lines(self, max_lines: int | None) -> None:
        self.max_lines = max_lines
        self.refresh(layout=True)


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
