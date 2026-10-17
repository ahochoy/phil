"""Links Phil prints: on a terminal the URL is a hyperlink to itself (OSC 8), so it can be clicked
even where the terminal doesn't spot URLs on its own; elsewhere it's the plain URL."""

from rich.style import Style
from rich.text import Text

from phil.text import printable


def link(url: str) -> Text:
    shown = printable(url)
    return Text(shown, style=Style(link=shown))


def opened_pr_line(number: int | str, url: str) -> Text:
    return Text.assemble(f"Opened PR #{number}: ", link(url))
