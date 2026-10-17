"""`phil parked`: list the parking lot's open items, and drop them or mark them done."""

import sqlite3

import typer
from rich.markup import escape
from rich.text import Text

from phil.parking import Closing, close_lines
from phil.store.db import connect
from phil.store.parked import list_parked
from phil.store.paths import ProjectPaths
from phil.ui.theme import make_console

parked_app = typer.Typer(help="List the parking lot's open items, and drop them or mark them done.")
console = make_console()


def _conn(ctx: typer.Context) -> sqlite3.Connection:
    from phil.cli import main  # main registers this app, so it's imported here, not at the top

    return connect(ProjectPaths(main._resolve(ctx).slug).db_path)


def _close(ctx: typer.Context, item_ids: list[str] | None, every: bool, status: Closing, verb: str) -> None:
    if every and item_ids:
        console.print(Text("Name items or use --all, not both."))
        raise typer.Exit(1)
    if not every and not item_ids:
        console.print(Text(f"Name the items to {verb} (phil parked lists them), or use --all."))
        raise typer.Exit(1)
    lines, ok = close_lines(_conn(ctx), item_ids or [], status, every=every)
    for line in lines:
        console.print(Text(line), soft_wrap=True)
    if not ok:
        raise typer.Exit(1)


@parked_app.callback(invoke_without_command=True)
def show(ctx: typer.Context) -> None:
    """List open parking-lot items for the current repository."""
    if ctx.invoked_subcommand is not None:
        return
    items = list_parked(_conn(ctx))
    if not items:
        console.print("[phil.muted]Parking lot is empty.[/]")
        return
    for item in items:
        console.print(
            f"[phil.id]{escape(item.id)}[/] {escape(item.note)} "
            f"[phil.muted]({escape(item.raised_by)}: {escape(item.why_not_now)})[/]"
        )


@parked_app.command()
def drop(
    ctx: typer.Context,
    item_ids: list[str] | None = typer.Argument(None, metavar="[ID]...", help="Items to drop, such as P-001 or 1."),
    every: bool = typer.Option(False, "--all", help="Drop every open item."),
) -> None:
    """Drop parking-lot items you won't do."""
    _close(ctx, item_ids, every, "dropped", "drop")


@parked_app.command()
def done(
    ctx: typer.Context,
    item_ids: list[str] | None = typer.Argument(None, metavar="[ID]...", help="Items done, such as P-001 or 1."),
    every: bool = typer.Option(False, "--all", help="Mark every open item done."),
) -> None:
    """Mark parking-lot items done."""
    _close(ctx, item_ids, every, "promoted", "mark done")
