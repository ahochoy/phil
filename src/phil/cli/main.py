import importlib
import json
import os
import sqlite3
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.markup import escape
from rich.table import Table

from phil import __version__
from phil.config import ConfigError, load_config
from phil.contracts import Plan
from phil.contracts.schema import export_schemas
from phil.git import GitError, git
from phil.repo import RepoError, RepoInfo, resolve_repo
from phil.run.launch import is_worker_alive, prepare_run, spawn_worker
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


def _factory_from_env():
    target = os.environ.get("PHIL_AGENT_FACTORY")
    if not target:
        return None
    module_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_name), attr)()


@app.command("run")
def run_plan(
    ctx: typer.Context,
    plan_file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Plan JSON file."),
    base: str | None = typer.Option(None, "--base", help="Start from this ref instead of HEAD."),
    foreground: bool = typer.Option(False, "--foreground", help="Run in this process instead of a background worker."),
) -> None:
    """Start a run from a plan file."""
    info, _ = _open_project(ctx)
    try:
        plan = Plan.model_validate_json(plan_file.read_text())
    except ValidationError as exc:
        console.print(f"[phil.error]invalid plan: {escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    try:
        config = load_config(info.root)
    except ConfigError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    if not (plan.test_cmd or config.project.test_cmd):
        console.print("[phil.error]plan has no test_cmd and phil.toml sets no [project] test_cmd[/]")
        raise typer.Exit(1)
    if base is not None:
        try:
            base_sha = git(info.root, "rev-parse", f"{base}^{{commit}}").strip()
        except GitError as exc:
            console.print(f"[phil.error]{escape(str(exc))}[/]")
            raise typer.Exit(1) from exc
    else:
        base_sha = info.head_sha
        if info.dirty_files:
            count = len(info.dirty_files)
            console.print(
                f"[phil.warn]⚠ {count} uncommitted file{'s' if count != 1 else ''} not included "
                f"(the run starts from {base_sha[:8]})[/]"
            )
    if config.git.sign_commits is not False or config.git.run_hooks:
        console.print("[phil.muted]Commit signing or hooks are on; a failing signature or hook will pause the run.[/]")
    record = prepare_run(info, plan, base_sha)
    if foreground:
        from phil.run.worker import WorkerError, run_worker

        try:
            outcome = run_worker(info.root, record.run_id, "start", factory=_factory_from_env())
        except WorkerError as exc:
            console.print(f"[phil.error]{escape(str(exc))}[/]")
            raise typer.Exit(2) from exc
        except Exception as exc:
            console.print(
                f"[phil.error]Run {escape(record.run_id)} failed: "
                f"{escape(type(exc).__name__)}: {escape(str(exc))}[/]"
            )
            console.print(f"Continue with `phil resume {escape(record.run_id)}`.")
            raise typer.Exit(1) from exc
        console.print(f"Run [phil.id]{escape(record.run_id)}[/]: {escape(outcome.status)}")
        return
    spawn_worker(info.root, record.run_id, "start")
    console.print(
        f"Run [phil.id]{escape(record.run_id)}[/] started. Follow it with `phil attach {escape(record.run_id)}`."
    )


@app.command("_worker", hidden=True)
def worker(
    ctx: typer.Context,
    run_id: str,
    mode: str = typer.Option(..., "--mode"),
    decision: str | None = typer.Option(None, "--decision"),
) -> None:
    """Drive a run until it pauses, finishes, stops, or fails (internal)."""
    from phil.run.worker import WorkerError, run_worker

    start = ctx.obj.get("repo") or Path.cwd()
    try:
        outcome = run_worker(start, run_id, mode, json.loads(decision) if decision else None, factory=_factory_from_env())
    except WorkerError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    console.print(f"{escape(run_id)}: {escape(outcome.status)}")
