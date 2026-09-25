import sqlite3

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from phil.store.runs import list_runs
from phil.store.telemetry import run_totals


def _format_tokens(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


def render_runs(console: Console, conn: sqlite3.Connection) -> None:
    """Print the `run  plan  done  state  tokens  cost` table, or "No runs yet."."""
    records = list_runs(conn)
    if not records:
        console.print("[phil.muted]No runs yet.[/]")
        return
    table = Table(box=None, pad_edge=False)
    for column in ("run", "plan", "done", "state", "tokens", "cost"):
        table.add_column(column, style="phil.muted", no_wrap=True)
    for run in records:
        tokens, cost = run_totals(conn, run.run_id)
        table.add_row(
            f"[phil.id]{escape(run.run_id)}[/]",
            escape(run.keyword),
            f"{run.tasks_done}/{run.tasks_total}",
            escape(run.state),
            _format_tokens(tokens),
            f"[phil.cost]${cost:.2f}[/]",
        )
    console.print(table)
