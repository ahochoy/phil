"""The full-screen frame's widgets (the tree is in `app.py`'s `PhilApp.compose`)."""

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual import events
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static, TextArea

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


class Item(Static):
    """One printed renderable in the conversation."""

    def __init__(self, renderable: RenderableType) -> None:
        super().__init__(Themed(renderable))


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
    """The live line under the conversation (content comes with the live status); hidden if empty."""


class DecisionMenu(Static):
    """A docked decision callout; hidden unless a choice is being asked."""


class PromptBox(TextArea):
    """The input box. Any key typed here returns the conversation to the bottom."""

    async def _on_key(self, event: events.Key) -> None:
        self.screen.query_one(Conversation).to_bottom()
        await super()._on_key(event)
