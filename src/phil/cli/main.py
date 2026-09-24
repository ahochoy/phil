import sqlite3
from pathlib import Path

import typer
from rich.markup import escape
from rich.table import Table

from phil import __version__
from phil.contracts.schema import export_schemas
from phil.repo import RepoError, RepoInfo, resolve_repo
from phil.store.db import connect
from phil.store.parked import list_parked
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from phil.store.telemetry import run_totals
from phil.ui.theme import make_console

app = typer.Typer(add_completion=False, help="Phil: a contract-driven coding agent.")
console = make_console()


def _print_version(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Show version and exit."
    ),
    repo: Path | None = typer.Option(None, "--repo", help="Target repository (default: current directory)."),
) -> None:
    ctx.obj = {"repo": repo}
    if ctx.invoked_subcommand is None:
        console.print("[phil.muted]Chat mode is not implemented yet. Try `phil runs`.[/]")


def _open_project(ctx: typer.Context) -> tuple[RepoInfo, sqlite3.Connection]:
    start = ctx.obj.get("repo") or Path.cwd()
    try:
        info = resolve_repo(start)
    except RepoError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    return info, connect(ProjectPaths(info.slug).db_path)


def _format_tokens(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


@app.command()
def runs(ctx: typer.Context) -> None:
    """List runs for the current repository."""
    _, conn = _open_project(ctx)
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


@app.command()
def parked(ctx: typer.Context) -> None:
    """List open parking-lot items for the current repository."""
    _, conn = _open_project(ctx)
    items = list_parked(conn)
    if not items:
        console.print("[phil.muted]Parking lot is empty.[/]")
        return
    for item in items:
        console.print(
            f"[phil.id]{escape(item.id)}[/] {escape(item.note)} "
            f"[phil.muted]({escape(item.raised_by)}: {escape(item.why_not_now)})[/]"
        )


@app.command()
def schema(out: Path = typer.Option(Path("phil-schemas"), "--out", help="Output directory.")) -> None:
    """Export JSON Schema for every contract."""
    paths = export_schemas(out)
    console.print(f"Wrote [phil.id]{len(paths)}[/] schemas to {escape(str(out))}")
