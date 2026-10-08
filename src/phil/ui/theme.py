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
        "phil.band.start": "bold cyan on grey15",
        "phil.band.pass": "green on grey15",
        "phil.band.fail": "red on grey15",
        "phil.band.wait": "yellow on grey15",
        "phil.ref": "blue",
        "phil.callout.border.question": "cyan", "phil.callout.border.confirm": "cyan",
        "phil.callout.border.approval": "yellow", "phil.callout.border.pause": "yellow", "phil.callout.border.failure": "red",
        "phil.callout.title.question": "bold cyan", "phil.callout.title.confirm": "bold cyan",
        "phil.callout.title.approval": "bold yellow", "phil.callout.title.pause": "bold yellow", "phil.callout.title.failure": "bold red",
        "phil.callout.body": "default", "phil.callout.option": "default", "phil.callout.selected": "bold reverse",
        "phil.callout.hint": "dim", "phil.callout.key": "blue",
    }
)

STYLE_NAMES: tuple[str, ...] = tuple(name for name in PHIL_THEME.styles if name.startswith("phil."))


def make_console(**kwargs: Any) -> Console:
    return Console(theme=PHIL_THEME, **kwargs)
