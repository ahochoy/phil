"""`phil memory` (spec 2026-10-15 §2.4): show Phil's notes about this repo, add, remove, edit or clear
them, and apply or dismiss the suggestions for AGENTS.md. Memory text is plain text, never markup."""

import typer
from rich.text import Text

from phil.memory.agents_md import (
    active_run,
    apply_suggestions,
    clear_suggestions,
    read_suggestions,
)
from phil.memory.apply import check_line
from phil.memory.doc import SECTIONS, Bullet, MemoryDoc
from phil.memory.editor import EditorError, edit_file
from phil.memory.listing import memory_lines
from phil.memory.store import MemoryWriteError, update_memory, write_atomic
from phil.repo import RepoInfo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.text import printable
from phil.ui.theme import make_console

memory_app = typer.Typer(help="Show and change what Phil remembers about this repo.")
console = make_console()

DEFAULT_SECTION = "Conventions"


def _say(line: str) -> None:
    console.print(Text(line), soft_wrap=True)


def _fail(line: str) -> None:
    _say(line)
    raise typer.Exit(1)


def _project(ctx: typer.Context) -> tuple[RepoInfo, ProjectPaths]:
    from phil.cli import main  # main registers this app, so it's imported here, not at the top

    info = main._resolve(ctx)
    return info, ProjectPaths(info.slug)


def _show(paths: ProjectPaths) -> None:
    try:
        lines = memory_lines(paths)
    except OSError as exc:
        _fail(f"Can't read {paths.memory_path}: {printable(exc)}")
    for line in lines:
        _say(line)


def _update(paths: ProjectPaths, change):
    try:
        return update_memory(paths, change)
    except MemoryWriteError as exc:
        _fail(printable(exc))


def _confirm(question: str) -> bool:
    return typer.confirm(question, default=False)


@memory_app.callback(invoke_without_command=True)
def show(ctx: typer.Context) -> None:
    """Show what Phil remembers about this repo."""
    if ctx.invoked_subcommand is not None:
        return
    _, paths = _project(ctx)
    _show(paths)


@memory_app.command()
def add(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="One line to remember."),
    section: str = typer.Option(DEFAULT_SECTION, "--section", "-s", help=f"One of {', '.join(SECTIONS)}."),
) -> None:
    """Add a line of your own to Phil's memory."""
    _, paths = _project(ctx)
    name = next((known for known in SECTIONS if known.casefold() == section.strip().casefold()), None)
    if name is None:
        _fail(f"Unknown section {printable(section)}. Sections: {', '.join(SECTIONS)}.")
    problem = check_line(text)
    if problem is not None:
        _fail(f"Not added: {problem}.")
    _update(paths, lambda doc: doc.add(Bullet(name, text.strip(), False)))
    _say(f"Added to {name}.")


@memory_app.command()
def remove(ctx: typer.Context, number: int = typer.Argument(..., help="The bullet's number, as `phil memory` lists it.")) -> None:
    """Remove one line from Phil's memory."""
    _, paths = _project(ctx)

    def take(doc: MemoryDoc) -> Bullet | None:
        try:
            return doc.remove_index(number)
        except IndexError:
            return None

    removed = _update(paths, take)
    if removed is None:
        _fail(f"No bullet {number}. phil memory lists them.")
    _say(f"Removed: {printable(removed.text)}")


@memory_app.command()
def edit(ctx: typer.Context) -> None:
    """Open Phil's memory file in your editor."""
    info, paths = _project(ctx)
    path = paths.memory_path
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, MemoryDoc.new(info.slug).render().encode("utf-8"))
        except OSError as exc:
            _fail(f"Can't write {path}: {printable(exc)}")
    try:
        edit_file(str(path))
    except EditorError as exc:
        _fail(f"Couldn't open the editor: {printable(exc)}")
    _show(paths)


@memory_app.command()
def clear(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", help="Clear without asking."),
) -> None:
    """Remove every line from Phil's memory (headings and other text stay)."""
    _, paths = _project(ctx)
    if not yes and not _confirm("Remove every bullet from Phil's memory for this repo?"):
        _say("Nothing changed.")
        return

    def wipe(doc: MemoryDoc) -> int:
        count = len(doc.bullets())
        doc.entries[:] = [entry for entry in doc.entries if not isinstance(entry, Bullet)]
        return count

    _say(f"Cleared {_update(paths, wipe)} bullet(s).")


@memory_app.command()
def suggestions(ctx: typer.Context) -> None:
    """List the lines Phil suggests adding to AGENTS.md."""
    _, paths = _project(ctx)
    pending = read_suggestions(paths)
    if not pending:
        _say("No suggestions for AGENTS.md.")
        return
    for n, line in enumerate(pending, 1):
        _say(f"{n}. {printable(line)}")


@memory_app.command()
def apply(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", help="Apply without asking (including creating AGENTS.md)."),
) -> None:
    """Append the suggestions to this repo's AGENTS.md. Nothing is committed."""
    info, paths = _project(ctx)
    conn = connect(paths.db_path)
    try:
        running = active_run(conn)
    finally:
        conn.close()
    if running is not None:
        _fail(f"Run {running} is in progress; apply suggestions when it finishes.")
    pending = read_suggestions(paths)
    if not pending:
        _fail("no suggestions")
    for n, line in enumerate(pending, 1):
        _say(f"{n}. {printable(line)}")
    if not yes and not _confirm("Append these to AGENTS.md?"):
        _say("Nothing changed.")
        return
    create = False
    if not (info.root / "AGENTS.md").exists():
        if not yes and not _confirm("There's no AGENTS.md. Create it?"):
            _say("Nothing changed.")
            return
        create = True
    try:
        result = apply_suggestions(info.root, paths, create=create)
    except (ValueError, FileNotFoundError, MemoryWriteError) as exc:
        _fail(printable(exc))
    _say(f"Added {len(result.lines)} line(s) to {result.path}. Not committed: review and commit it yourself.")
    if result.memory_error:
        _say(f"Couldn't tidy memory: {printable(result.memory_error)}")


@memory_app.command()
def dismiss(ctx: typer.Context) -> None:
    """Throw away the pending AGENTS.md suggestions."""
    _, paths = _project(ctx)
    try:
        count = clear_suggestions(paths)
    except MemoryWriteError as exc:
        _fail(printable(exc))
    _say(f"Dismissed {count} suggestion(s).")
