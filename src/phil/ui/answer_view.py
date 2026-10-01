from rich.console import Console
from rich.markup import escape

from phil.contracts.routing import Answer


def render_answer(console: Console, answer: Answer) -> None:
    console.print(escape(answer.text))
    if answer.files:
        console.print(f"[phil.muted]Files: {escape(', '.join(answer.files))}[/]")
