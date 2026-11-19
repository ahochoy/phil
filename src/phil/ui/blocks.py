"""`BlockConsole`: the chat's console with breathing room. Each top-level print starts a block,
with one blank line before it; no two blank lines in a row and none at the very top."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from phil.ui.bursts import one_line


NARROW = 10  # under this many cells beside the indent and lead, a wrapped line stops hanging


def _chunks(text: str, room: int) -> list[str]:
    """`text` broken at spaces into pieces of at most `room` cells; a word longer than that is cut."""
    lines: list[str] = []
    line = ""
    for word in text.split(" "):
        while cell_len(word) > room:  # a word too long for any line: fill the current one, cut the rest
            if line:
                lines.append(line)
                line = ""
            cut = ""
            for char in word:
                if cell_len(cut + char) > room and cut:
                    break
                cut += char
            lines.append(cut)
            word = word[len(cut):]
        joined = f"{line} {word}" if line else word
        if line and cell_len(joined) > room:
            lines.append(line)
            joined = word
        line = joined
    if line:
        lines.append(line)
    return lines


def hanging(text: str, width: int, *, indent: str, lead: str = "", style: str = "") -> list[Text]:
    """`text` wrapped to `width` as plain `Text` lines (never `Padding`: the frame selects `Text`).
    The first line is `indent + lead + text`; the rest are indented by `indent` and spaces the
    width of `lead`; when that leaves under NARROW cells, the later lines have neither and use the
    whole width. Every line is at most `width - 1` cells. Hidden characters show as `?`."""
    first, rest = indent + lead, indent + " " * cell_len(lead)
    flat = one_line(text)
    room = max(1, width - 1 - cell_len(first))
    if width - 1 - cell_len(rest) >= NARROW:
        return [Text((first if n == 0 else rest) + chunk, style=style) for n, chunk in enumerate(_chunks(flat, room))]
    head = _chunks(flat, room)[:1]  # too narrow to hang: the first line only, the rest at full width
    lines = [Text(first + chunk, style=style) for chunk in head]
    tail = flat[len(head[0]):].lstrip(" ") if head else ""
    return lines + [Text(chunk, style=style) for chunk in _chunks(tail, max(1, width - 1))]


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
