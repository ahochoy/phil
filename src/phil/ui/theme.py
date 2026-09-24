from typing import Any

from rich.console import Console
from rich.theme import Theme

PHIL_THEME = Theme(
    {
        "phil.brand": "bold cyan",
        "phil.agent": "cyan",
        "phil.user": "bold white",
        "phil.muted": "dim",
        "phil.id": "bold blue",
        "phil.warn": "yellow",
        "phil.error": "bold red",
        "phil.gate.pass": "green",
        "phil.gate.fail": "red",
        "phil.cost": "magenta",
    }
)

STYLE_NAMES: tuple[str, ...] = tuple(name for name in PHIL_THEME.styles if name.startswith("phil."))


def make_console(**kwargs: Any) -> Console:
    return Console(theme=PHIL_THEME, **kwargs)
