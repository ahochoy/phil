from rich.console import Console
from rich.markup import escape

from phil.contracts import Brief


def render_brief(console: Console, brief: Brief, *, numbered: bool = False) -> None:
    """With `numbered`, detail refs are numbered for the chat's `/more <n>`."""
    console.print(f"[phil.brand]{escape(brief.headline)}[/]")
    if brief.status:
        console.print(f"[phil.muted]{escape(brief.status)}[/]")
    for point in brief.points:
        console.print(f"  • {escape(point)}")
    for decision in brief.needs_you:
        options = escape("[" + " / ".join(decision.options) + "]")
        console.print(f"  [phil.warn]? {escape(decision.question)} {options}[/]")
    for number, ref in enumerate(brief.details, start=1):
        prefix = f"{number} " if numbered else ""
        console.print(f"  [phil.muted]→ {prefix}{escape(ref.label)}: {escape(ref.path)}[/]")
