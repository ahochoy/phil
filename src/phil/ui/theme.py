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

# The Rich colour names the theme's prompt styles use, as prompt_toolkit's ANSI colours.
_PT_COLORS = {name: f"ansi{name}" for name in ("cyan", "yellow", "red", "blue", "green", "magenta", "white")}
_PT_ATTRS = {"bold", "dim", "reverse", "italic", "underline"}


def prompt_toolkit_style(rich_style: str) -> str:
    """A Rich style string (e.g. `"bold yellow"`) as a prompt_toolkit one (`"bold ansiyellow"`).

    Covers only what the prompt shows (the callout styles and the muted live row): attributes and
    plain foreground colours; `default` means no style. Anything else raises, so a theme change
    that the prompt can't show is caught by the tests rather than rendered wrong."""
    parts = []
    for word in rich_style.split():
        if word in _PT_ATTRS:
            parts.append(word)
        elif word in _PT_COLORS:
            parts.append(_PT_COLORS[word])
        elif word != "default":
            raise ValueError(f"no prompt_toolkit style for {word!r} in {rich_style!r}")
    return " ".join(parts)


def prompt_toolkit_styles() -> dict[str, str]:
    """The prompt's style rules: every `phil.callout.*` style, and `live` (the muted live row)."""
    rules = {
        name: prompt_toolkit_style(str(style))
        for name, style in PHIL_THEME.styles.items()
        if name.startswith("phil.callout.")
    }
    rules["live"] = prompt_toolkit_style(str(PHIL_THEME.styles["phil.muted"]))
    return rules


def make_console(**kwargs: Any) -> Console:
    return Console(theme=PHIL_THEME, **kwargs)
