import sqlite3

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from phil.store.runs import list_runs
from phil.store.telemetry import format_cost, run_usage


def _format_tokens(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


def _pr_marker(run) -> str:
    if run.pr_number is None:
        return ""
    if run.pr_state in ("merged", "closed"):
        return f" · {run.pr_state} #{run.pr_number}"
    return f" · PR #{run.pr_number}"


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
        totals = run_usage(conn, run.run_id)
        table.add_row(
            f"[phil.id]{escape(run.run_id)}[/]",
            escape(run.keyword),
            f"{run.tasks_done}/{run.tasks_total}",
            escape(run.state + _pr_marker(run)),
            _format_tokens(totals.tokens),
            f"[phil.cost]{escape(format_cost(totals.cost_usd, totals.cost_source))}[/]",
        )
    console.print(table)
