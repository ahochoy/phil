from rich.console import Console
from rich.markup import escape

from phil.contracts import Brief


def render_brief(console: Console, brief: Brief) -> None:
    console.print(f"[phil.brand]{escape(brief.headline)}[/]")
    if brief.status:
        console.print(f"[phil.muted]{escape(brief.status)}[/]")
    for point in brief.points:
        console.print(f"  • {escape(point)}")
    for decision in brief.needs_you:
        options = escape("[" + " / ".join(decision.options) + "]")
        console.print(f"  [phil.warn]? {escape(decision.question)} {options}[/]")
    for ref in brief.details:
        console.print(f"  [phil.muted]→ {escape(ref.label)}: {escape(ref.path)}[/]")
