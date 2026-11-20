from collections.abc import Callable
from typing import Any

from rich.console import Console, ConsoleDimensions, Group, RenderableType
from rich.text import Text

from phil.ui.theme import PHIL_THEME


class PaneConsole(Console):
    """A Rich console whose prints become conversation items.

    `print` turns its objects into one renderable and hands it to `post`; `width` is the pane's
    width, read from `width()` each time. There is no input: the frame reads through `ChatIO`."""

    def __init__(self, post: Callable[[RenderableType], None], width: Callable[[], int]) -> None:
        super().__init__(theme=PHIL_THEME)
        self._post = post
        self._pane_width = width

    @property
    def size(self) -> ConsoleDimensions:
        return ConsoleDimensions(self._pane_width(), self._height or 25)

    def print(
        self,
        *objects: Any,
        sep: str = " ",
        markup: bool | None = None,
        emoji: bool | None = None,
        highlight: bool | None = None,
        **_ignored: Any,
    ) -> None:
        parts: list[RenderableType] = [
            self.render_str(obj, markup=markup, emoji=emoji, highlight=highlight) if isinstance(obj, str) else obj
            for obj in objects
        ]
        if all(isinstance(part, Text) for part in parts):
            self._post(Text(sep).join(parts))  # type: ignore[arg-type]
        else:
            self._post(Group(*parts))

    def input(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("the full-screen frame reads input through ChatIO, not the console")
