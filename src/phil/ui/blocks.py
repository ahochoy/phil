"""`BlockConsole`: the chat's console with breathing room. Each top-level print starts a block,
with one blank line before it; no two blank lines in a row and none at the very top."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.text import Text


def _is_blank(objects: tuple[Any, ...]) -> bool:
    return all((isinstance(obj, str) and not obj) or (isinstance(obj, Text) and not obj.plain) for obj in objects)


class BlockConsole:
    """Wraps `console`; every other attribute is the wrapped console's. A print starts a block
    unless it is inside `continued()` or is a later print inside `group()`."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._printed = False  # anything above (printed here, or marked)
        self._last_blank = False  # the last line above is blank
        self._grouping = 0
        self._group_started = False  # the open group's block has begun
        self._continuing = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._console, name)

    def print(self, *objects: Any, **kwargs: Any) -> None:
        if _is_blank(objects):  # a blank line asked for: never doubled, never on top
            self.blank()
            return
        if not self._continuing and not (self._grouping and self._group_started):
            self.blank()
        if self._grouping:
            self._group_started = True
        self._console.print(*objects, **kwargs)
        self._printed, self._last_blank = True, False

    @contextmanager
    def group(self) -> Iterator[None]:
        """The prints inside form one block: one blank line before the first."""
        if not self._grouping:  # a group inside a group is part of it
            self._group_started = False
        self._grouping += 1
        try:
            yield
        finally:
            self._grouping -= 1

    @contextmanager
    def continued(self) -> Iterator[None]:
        """The prints inside start no block: they join the one above."""
        self._continuing += 1
        try:
            yield
        finally:
            self._continuing -= 1

    def blank(self) -> None:
        """One blank line, unless the last line is blank or nothing is above yet."""
        if self._printed and not self._last_blank:
            self._console.print()
            self._last_blank = True

    def mark_blank(self) -> None:
        """A blank line was printed some other way."""
        self._printed, self._last_blank = True, True

    def mark_line(self) -> None:
        """A line was printed some other way (the user's typed line): the next block gets its blank."""
        self._printed, self._last_blank = True, False
