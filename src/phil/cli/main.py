import importlib
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.markup import escape

from phil import __version__
from phil.chat.approval import git_policy_note, launch_problems, terminated
from phil.config import (
    CHAT_ROLES,
    RUN_ROLES,
    ConfigError,
    PhilConfig,
    effective_toml,
    global_config_path,
    load_config,
)
from phil.contracts import Plan
from phil.contracts.schema import export_schemas
from phil.git import GitError, git
from phil.repo import RepoError, RepoInfo, resolve_repo
from phil.run.launch import is_worker_alive, prepare_run, spawn_worker, worker_starting
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.parked import list_parked
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run, update_run
from phil.ui.runs_view import render_runs
from phil.ui.theme import make_console

logger = logging.getLogger(__name__)
# Background sweep failures are logged for developers, never printed: without a handler here,
# Python's last-resort handler would write them to stderr. They still propagate to `phil`.
logger.addHandler(logging.NullHandler())

app = typer.Typer(add_completion=False, help="Phil: a contract-driven coding agent.")
models_app = typer.Typer(help="Check the configured models.")
app.add_typer(models_app, name="models")
console = make_console()

# A pending run whose row was updated more recently than this is assumed to still be starting
# (its worker process hasn't written a pid/heartbeat yet); older than this, treat it as a worker
# that never started and let `phil resume` continue it from scratch.
PENDING_STALE_AFTER_S = 30.0

SET_HELP = "Override a setting for this command, e.g. --set run.max_cost_usd=5 (repeatable)."


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
    base: str | None = typer.Option(
        None, "--base", help="Chat only: start the chat's runs from this ref instead of HEAD."
    ),
    resume_chat: str | None = typer.Option(
        None, "--resume", help="Reopen a chat by id (see the list shown by `phil`)."
    ),
    new: bool = typer.Option(False, "--new", help="Start a new chat without listing open ones."),
    overrides: list[str] | None = typer.Option(None, "--set", help=SET_HELP),
) -> None:
    ctx.obj = {"repo": repo, "base": base, "resume": resume_chat, "new": new, "overrides": list(overrides or [])}
    if ctx.invoked_subcommand is None:
        _chat(ctx)


def _resolve(ctx: typer.Context) -> RepoInfo:
    start = ctx.obj.get("repo") or Path.cwd()
    try:
        return resolve_repo(start)
    except RepoError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc


def _open_project(ctx: typer.Context) -> tuple[RepoInfo, sqlite3.Connection]:
    info = _resolve(ctx)
    return info, connect(ProjectPaths(info.slug).db_path)


def _load_config(root: Path, overrides: list[str]) -> PhilConfig:
    try:
        return load_config(root, overrides=overrides)
    except ConfigError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc


def _require_api_keys(config: PhilConfig, roles: tuple[str, ...]) -> None:
    problems = config.missing_keys(roles, os.environ)
    if problems:
        for problem in problems:
            console.print(f"[phil.error]{escape(problem)}[/]")
        raise typer.Exit(1)


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _terminal(toolbar):
    """The live terminal IO (patched in tests, where CliRunner has no real terminal)."""
    from phil.chat.terminal import TerminalIO

    return TerminalIO(toolbar)


def _open_chat_line(n: int, chat) -> str:
    objective = escape(chat.objective) if chat.objective else "[phil.muted](no goal)[/]"
    if chat.run_id:
        status = f"run {escape(chat.run_id)} {escape(chat.run_state or 'unknown')}"
    else:
        status = "plan waiting for approval"
    elsewhere = " · [phil.warn]open elsewhere[/]" if chat.open_elsewhere else ""
    return f"{n}. [phil.id]{escape(chat.id)}[/] · {objective} · {status}{elsewhere}"


def _choose_open_chat(out, chats, ask) -> str | None:
    """List open chats and ask which to reopen; None starts a new chat."""
    out.print("Open chats:")
    for n, chat in enumerate(chats, 1):
        out.print(_open_chat_line(n, chat))
    while True:
        try:
            answer = ask("Reopen one? [number / Enter for a new chat] › ")
        except EOFError:
            raise typer.Exit(0) from None
        answer = answer.strip()
        if not answer:
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(chats):
            return chats[int(answer) - 1].id
        out.print(f"[phil.error]choose 1–{len(chats)}, or press Enter for a new chat[/]")


def _reopen_chat(session_cls, paths: ProjectPaths, chat_id: str):
    try:
        return session_cls.open(paths, chat_id)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[phil.error]cannot reopen: {escape(str(exc))}[/]")
        raise typer.Exit(1) from exc


def _chat(ctx: typer.Context) -> None:
    from phil.chat.controller import HELP
    from phil.chat.session import ChatLocked, ChatSession, chat_logging, list_open_chats

    resume_id, new = ctx.obj.get("resume"), ctx.obj.get("new", False)
    if resume_id is not None and new:
        console.print("[phil.error]choose --resume or --new, not both[/]")
        raise typer.Exit(1)
    info, conn = _open_project(ctx)
    overrides = ctx.obj.get("overrides", [])
    config = _load_config(info.root, overrides)
    missing = config.missing_model_messages(CHAT_ROLES)
    if missing:
        for message in missing:
            console.print(f"[phil.error]{escape(message)}[/]")
        raise typer.Exit(1)
    # The chat starts runs too, and their worker inherits this environment.
    _require_api_keys(config, CHAT_ROLES + RUN_ROLES)
    paths = ProjectPaths(info.slug)
    session = _reopen_chat(ChatSession, paths, resume_id) if resume_id is not None else None
    base = ctx.obj.get("base")
    base_sha: str | None
    if base is not None:
        try:
            base_sha = git(info.root, "rev-parse", f"{base}^{{commit}}").strip()
        except GitError as exc:
            console.print(f"[phil.error]{escape(str(exc))}[/]")
            raise typer.Exit(1) from exc
        base_label = base
        header_sha = base_sha
    else:
        base_sha, base_label = None, info.branch or "detached"
        header_sha = info.head_sha
    tty = _is_tty()
    # Under patch_stdout, stdout is a proxy; force colour so Rich keeps emitting it.
    out = make_console(force_terminal=True) if tty else console
    out.print(
        f"[phil.brand]Phil[/] · {escape(info.root.name)} · base: {escape(base_label)} @ {escape(header_sha[:7])}"
    )
    if base is None and info.dirty_files:
        count = len(info.dirty_files)
        out.print(
            f"[phil.warn]⚠ {count} uncommitted file{'s' if count != 1 else ''} — not included in runs[/]"
        )
    parked_count = len(list_parked(conn))
    if parked_count:
        out.print(f"[phil.muted]{parked_count} parked[/]")
    if session is None and not new and tty:
        chats = list_open_chats(paths, conn)
        if chats:
            chosen = _choose_open_chat(out, chats, lambda prompt: out.input(f"[phil.user]{escape(prompt)}[/]"))
            if chosen is not None:
                session = _reopen_chat(ChatSession, paths, chosen)
    out.print(f"[phil.muted]{escape(HELP)}[/]")
    try:
        factory = _factory_from_env()
    except Exception as exc:
        out.print(
            f"[phil.error]cannot load PHIL_AGENT_FACTORY: {escape(type(exc).__name__)}: {escape(str(exc))}[/]"
        )
        raise typer.Exit(1) from exc

    resume = session is not None
    session = session or ChatSession.create(paths)
    try:
        session.lock()  # one window per chat: two writers would overwrite each other's state
    except ChatLocked as exc:
        out.print(f"[phil.error]Chat {escape(session.id)} is already open in another window (pid {exc.pid}).[/]")
        raise typer.Exit(1) from exc
    try:
        with chat_logging(session.dir):
            _run_chat(info, config, conn, out, tty, factory, base_sha, session, resume, overrides)
    finally:
        session.unlock()


def _run_chat(info, config, conn, out, tty, factory, base_sha, session, resume: bool, overrides=()) -> None:
    from phil.chat.controller import ChatController
    from phil.chat.terminal import LineIO
    from phil.ui.toolbar import render_toolbar

    controller = None

    def toolbar() -> str:
        if controller is None:
            return ""
        return render_toolbar(controller.state.view(), time.time(), width=terminal.width())

    terminal = _terminal(toolbar) if tty else LineIO(out)
    try:
        io = terminal.chat_io(lambda root, run_id, mode, decision=None: spawn_worker(root, run_id, mode, decision))
        try:
            controller = ChatController(
                info, config, conn, out, io, factory=factory, base_sha=base_sha, session=session, resume=resume,
                config_overrides=overrides,
            )
        except GitError as exc:
            out.print(f"[phil.error]{escape(str(exc))}[/]")
            raise typer.Exit(1) from exc
        terminal.run(controller.run)
    finally:
        terminal.close()


def _print_pr_changes(changes) -> None:
    from phil.publish.service import change_line

    for change in changes:
        style = "phil.muted" if change.kind == "merged" else "phil.warn"
        console.print(f"[{style}]{escape(change_line(change))}[/]")


def _sweep_quietly(info: RepoInfo, conn: sqlite3.Connection) -> None:
    """Notice merged/closed pull requests; never let a failure here break the command."""
    try:
        from phil.publish import publisher as publishing
        from phil.publish.service import sweep_prs

        changes = sweep_prs(info, conn, publishing.make_publisher(info.root))
    except Exception:  # never printed or fatal (see the NullHandler on `logger`)
        logger.warning("pull request sweep failed", exc_info=True)
        return
    _print_pr_changes(changes)


@app.command()
def runs(ctx: typer.Context) -> None:
    """List runs for the current repository."""
    info, conn = _open_project(ctx)
    _sweep_quietly(info, conn)
    render_runs(console, conn)


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
    run_overrides: list[str] | None = typer.Option(None, "--set", help=SET_HELP),
) -> None:
    """Start a run from a plan file."""
    info, _ = _open_project(ctx)
    # `phil --set a=1 run --set b=2`: both apply, the command's own last. The run keeps them.
    overrides = [*ctx.obj.get("overrides", []), *(run_overrides or [])]
    try:
        plan = Plan.model_validate_json(plan_file.read_text())
    except ValidationError as exc:
        console.print(f"[phil.error]invalid plan: {escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    config = _load_config(info.root, overrides)
    problems = launch_problems(plan, config, info.root)
    if problems:
        for problem in problems:
            console.print(f"[phil.error]{escape(terminated(problem))}[/]")
        raise typer.Exit(1)
    _require_api_keys(config, RUN_ROLES)
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
    note = git_policy_note(config)
    if note:
        console.print(f"[phil.muted]{escape(note)}[/]")
    factory = None
    if foreground:
        try:
            factory = _factory_from_env()
        except Exception as exc:
            console.print(
                f"[phil.error]cannot load PHIL_AGENT_FACTORY: {escape(type(exc).__name__)}: {escape(str(exc))}[/]"
            )
            raise typer.Exit(1) from exc
    record = prepare_run(info, plan, base_sha, overrides=overrides)
    if foreground:
        from phil.run.worker import WorkerError, run_worker

        try:
            outcome = run_worker(info.root, record.run_id, "start", factory=factory)
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


@app.command("config")
def config_command(
    ctx: typer.Context,
    path: bool = typer.Option(False, "--path", help="Show where the settings files are, and whether they exist."),
) -> None:
    """Show the effective settings, and where each value came from."""
    info = _resolve(ctx)
    if path:
        for label, file in (("global", global_config_path()), ("repo", info.root / "phil.toml")):
            state = "exists" if file.is_file() else "missing"
            console.print(f"{label}: {escape(str(file))} ({state})", soft_wrap=True, highlight=False)
        return
    typer.echo(effective_toml(_load_config(info.root, ctx.obj.get("overrides", []))), nl=False)


@models_app.command("check")
def models_check(ctx: typer.Context) -> None:
    """Make one tiny call to each configured model to check it returns structured output."""
    from phil.agents.check import check_models

    info = _resolve(ctx)
    config = _load_config(info.root, ctx.obj.get("overrides", []))
    try:
        factory = _factory_from_env()
    except Exception as exc:
        console.print(
            f"[phil.error]cannot load PHIL_AGENT_FACTORY: {escape(type(exc).__name__)}: {escape(str(exc))}[/]"
        )
        raise typer.Exit(1) from exc
    results = check_models(config, factory=factory, repo_root=info.root)
    if not results:
        console.print(
            "[phil.error]No models configured. Set models.high and models.low in "
            f"{escape(str(global_config_path()))} or phil.toml.[/]",
            soft_wrap=True,
        )
        raise typer.Exit(1)
    for result in results:
        if result.ok:
            line = f"[phil.gate.pass]✓[/] {escape(result.label)}  {escape(result.model)}  {result.seconds:.1f}s"
        else:
            line = f"[phil.gate.fail]✗[/] {escape(result.label)}  {escape(result.model)}  {escape(result.detail)}"
        console.print(line, soft_wrap=True, highlight=False)
    if not all(result.ok for result in results):
        raise typer.Exit(1)


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
    if outcome.status == "stopped":
        from phil.workspace import shell

        # A tool thread may have started a command after the stop handler's own kill.
        shell.kill_active_groups()
    console.print(f"{escape(run_id)}: {escape(outcome.status)}")


def _require_run(conn: sqlite3.Connection, run_id: str):
    record = get_run(conn, run_id)
    if record is None:
        console.print(f"[phil.error]unknown run {escape(run_id)}[/]")
        raise typer.Exit(1)
    return record


@app.command()
def resume(
    ctx: typer.Context,
    run_id: str,
    action: str | None = typer.Option(None, "--action", help="Answer a paused run (e.g. retry, skip, approve)."),
    hint: str | None = typer.Option(None, "--hint", help="Hint for the next attempt (with --action retry)."),
) -> None:
    """Answer a paused run, or continue a failed or stopped one."""
    info, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    if is_worker_alive(record) or worker_starting(run_events(ProjectPaths(info.slug), run_id)):
        console.print(f"[phil.error]{escape(run_id)} already has a running worker[/]")
        raise typer.Exit(1)
    if record.state == "escalated":
        latest = run_events(ProjectPaths(info.slug), run_id).latest("escalation")
        escalation = latest["escalation"] if latest else {"summary": record.needs_attention or "", "options": []}
        options = escalation["options"]
        if action is None:
            console.print(escape(escalation["summary"]))
            console.print(f"[phil.error]choose --action: {escape(', '.join(options))}[/]")
            raise typer.Exit(2)
        if action not in options:
            console.print(
                f"[phil.error]unknown action {escape(repr(action))}; choose one of: {escape(', '.join(options))}[/]"
            )
            raise typer.Exit(2)
        decision = {"action": action} | ({"hint": hint} if hint else {})
        spawn_worker(info.root, run_id, "resume", decision)
        console.print(f"Resuming [phil.id]{escape(run_id)}[/] with {escape(action)}.")
        return
    if record.state in ("pending", "failed", "stopped", "running"):
        if action is not None:
            console.print("[phil.error]nothing to answer; the run continues from its last checkpoint[/]")
            raise typer.Exit(2)
        if record.state == "pending":
            age = (datetime.now(UTC) - datetime.fromisoformat(record.updated_at)).total_seconds()
            if age <= PENDING_STALE_AFTER_S:
                console.print(
                    f"[phil.error]{escape(run_id)} is still starting; follow it with `phil attach {escape(run_id)}`[/]"
                )
                raise typer.Exit(1)
        spawn_worker(info.root, run_id, "continue")
        console.print(f"Continuing [phil.id]{escape(run_id)}[/] from its last checkpoint.")
        return
    console.print(f"[phil.error]{escape(run_id)} is {escape(record.state)}; nothing to resume[/]")
    raise typer.Exit(1)


@app.command()
def stop(
    ctx: typer.Context,
    run_id: str,
    timeout: float = typer.Option(15.0, "--timeout", help="Seconds to wait for the worker to stop."),
) -> None:
    """Stop a running run; continue it later with `phil resume`."""
    _, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    if record.state == "escalated":
        console.print(
            f"[phil.error]{escape(run_id)} is waiting for your decision; "
            f"stop it with `phil resume {escape(run_id)} --action abort`[/]",
            soft_wrap=True,
        )
        raise typer.Exit(1)
    if record.state not in ("running", "pending"):
        console.print(f"[phil.error]{escape(run_id)} is {escape(record.state)}; nothing to stop[/]")
        raise typer.Exit(1)
    alive = is_worker_alive(record)
    if not alive and record.state == "pending":
        age = (datetime.now(UTC) - datetime.fromisoformat(record.updated_at)).total_seconds()
        if age <= PENDING_STALE_AFTER_S:
            console.print(f"[phil.error]{escape(run_id)} is still starting; try again in a few seconds[/]")
            raise typer.Exit(1)
    if alive:
        try:
            os.kill(record.pid, signal.SIGTERM)
        except ProcessLookupError:
            alive = False
        except PermissionError as exc:
            console.print(f"[phil.error]{escape(str(exc))}[/]")
            raise typer.Exit(1) from exc
        else:
            deadline = time.monotonic() + timeout
            current = get_run(conn, run_id)
            while current is not None and current.state in ("running", "pending"):
                if time.monotonic() > deadline:
                    console.print("[phil.error]the worker did not stop in time[/]")
                    raise typer.Exit(1)
                time.sleep(0.2)
                current = get_run(conn, run_id)
            _finish_stop(run_id, current)
            return
    # No live worker (or it exited between the liveness check and the kill): mark the row
    # stopped ourselves, unless it's already settled into some other terminal state on its own.
    current = get_run(conn, run_id)
    if current is not None and current.state in ("running", "pending"):
        current = update_run(conn, run_id, state="stopped", needs_attention="stopped by user (worker was not running)")
    _finish_stop(run_id, current)


def _finish_stop(run_id: str, current) -> None:
    if current is None:
        console.print(f"[phil.error]unknown run {escape(run_id)}[/]")
        raise typer.Exit(1)
    if current.state != "stopped":
        console.print(f"[phil.error]{escape(run_id)} ended as {escape(current.state)}[/]")
        raise typer.Exit(1)
    console.print(f"Stopped [phil.id]{escape(run_id)}[/]. Continue with `phil resume {escape(run_id)}`.")


@app.command("attach")
def attach_command(ctx: typer.Context, run_id: str) -> None:
    """Follow a run and answer it when it pauses."""
    from phil.cli.attach import AttachIO, attach

    info, conn = _open_project(ctx)
    _require_run(conn, run_id)

    def choose(prompt: str, options: list[str]) -> str:
        choices = ", ".join(options)
        while True:
            answer = typer.prompt(f"{prompt} ({choices})")
            if answer in options:
                return answer
            console.print(f"[phil.error]choose one of: {escape(choices)}[/]")

    def ask_hint() -> str | None:
        return typer.prompt("Hint for the next attempt (optional)", default="", show_default=False) or None

    io = AttachIO(choose=choose, ask_hint=ask_hint, spawn=lambda mode, decision: spawn_worker(info.root, run_id, mode, decision))
    attach(conn, run_id, run_events(ProjectPaths(info.slug), run_id), console, io)


@app.command()
def diff(ctx: typer.Context, run_id: str) -> None:
    """Show the changes a run made, compared with its base."""
    info, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    try:
        typer.echo(git(info.root, "diff", record.base_sha, record.branch, "--"), nl=False)
    except GitError as exc:
        console.print("[phil.error]the run's branch no longer exists[/]")
        raise typer.Exit(1) from exc


@app.command("show")
def show_command(
    ctx: typer.Context,
    run_id: str,
    n: int | None = typer.Argument(None, help="Print detail #N in full instead of the overview."),
) -> None:
    """Show a run's tasks, usage, open issues, and numbered details."""
    from phil.ui import show_view
    from phil.ui.show_view import render_show, show_refs

    info, conn = _open_project(ctx)
    _require_run(conn, run_id)
    paths = ProjectPaths(info.slug)
    if n is None:
        _sweep_quietly(info, conn)
        render_show(console, conn, paths, run_id)
        return
    refs = show_refs(paths, run_id)
    if n < 1 or n > len(refs):
        console.print(f"[phil.error]No detail #{n} for {escape(run_id)}.[/]")
        raise typer.Exit(1)
    path = Path(refs[n - 1].path)
    try:
        text = show_view.detail_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        console.print(f"[phil.error]Couldn't read {escape(str(path))}: {escape(type(exc).__name__)}[/]")
        raise typer.Exit(1) from exc
    typer.echo(text)


@app.command()
def clean(
    ctx: typer.Context,
    run_id: str | None = typer.Argument(None, help="The run to clean up."),
    purge: bool = typer.Option(False, "--purge", help="Also delete the run summary."),
    merged: bool = typer.Option(False, "--merged", help="Clean up every run whose pull request has merged."),
) -> None:
    """Remove a finished run's worktree, branch, checkpoints, and scratch files."""
    from phil.run.cleanup import CleanError, clean_run

    if (run_id is None) == (not merged):
        console.print("[phil.error]Usage: phil clean <run-id> or phil clean --merged[/]")
        raise typer.Exit(2)
    info, conn = _open_project(ctx)
    if run_id is None:
        _clean_merged(info, conn)
        return
    record = _require_run(conn, run_id)
    if record.state in ("pending", "running", "escalated"):
        console.print(
            f"[phil.error]{escape(run_id)} is {escape(record.state)}; finish or stop it first "
            f"(`phil stop {escape(run_id)}` or `phil resume {escape(run_id)} --action abort`)[/]"
        )
        raise typer.Exit(1)
    paths = ProjectPaths(info.slug)
    if is_worker_alive(record) or worker_starting(run_events(paths, run_id)):
        console.print(f"[phil.error]{escape(run_id)} has a worker running; stop it first[/]")
        raise typer.Exit(1)
    try:
        clean_run(info, conn, record, purge=purge)
    except CleanError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    kept = "" if purge else " (kept summary.md and open_issues.json)"
    console.print(f"Cleaned [phil.id]{escape(run_id)}[/]{kept}.")


def _clean_merged(info: RepoInfo, conn: sqlite3.Connection) -> None:
    from phil.publish import publisher as publishing
    from phil.publish.service import sweep_prs

    publisher = publishing.make_publisher(info.root)
    reason = publisher.available()
    if reason is not None:
        console.print(f"[phil.error]{escape(reason)}[/]")
        raise typer.Exit(1)
    changes = sweep_prs(info, conn, publisher, force=True)
    if not changes:
        console.print("No merged pull requests to clean up.")
        return
    _print_pr_changes(changes)


@app.command("pr")
def pr_command(
    ctx: typer.Context,
    run_id: str,
    base: str | None = typer.Option(None, "--base", help="Base branch for the pull request."),
) -> None:
    """Push a completed run's branch and open its pull request."""
    from phil.publish import publisher as publishing
    from phil.publish.publisher import PublishError
    from phil.publish.service import PublishRefused, publish_run

    info, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    publisher = publishing.make_publisher(info.root)
    try:
        record = publish_run(info, conn, record, publisher, base=base)
    except (PublishRefused, PublishError) as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    console.print(f"Opened PR #{record.pr_number}: {escape(record.pr_url)}")
