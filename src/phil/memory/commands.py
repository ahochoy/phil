"""The memory commands `phil memory` and the chat's `/memory`, `/remember` and `/forget` share: they return
plain message strings (never markup) or raise `MemoryCommandError` carrying the message to show."""

from pathlib import Path

from phil.memory.agents_md import apply_suggestions, clear_suggestions, read_suggestions
from phil.memory.apply import check_line
from phil.memory.doc import SECTIONS, Bullet, MemoryDoc
from phil.memory.store import MemoryWriteError, update_memory
from phil.store.paths import ProjectPaths
from phil.text import printable

DEFAULT_SECTION = "Conventions"
NO_SUGGESTIONS = "No suggestions for AGENTS.md."


class MemoryCommandError(Exception):
    """A command that can't go ahead; `str(exc)` is the message to show."""


def section_named(name: str) -> str | None:
    """The section `name` names, ignoring case and surrounding spaces, or None."""
    return next((known for known in SECTIONS if known.casefold() == name.strip().casefold()), None)


def split_section(arg: str) -> tuple[str, str]:
    """`[section:] text` -> (section, text). Only a leading word followed by `:` that names a section
    picks it; otherwise the section is the default and the text is whole."""
    head, colon, rest = arg.strip().partition(":")
    if colon and " " not in head.strip():
        name = section_named(head)
        if name is not None:
            return name, rest.strip()
    return DEFAULT_SECTION, arg.strip()


def _update(paths: ProjectPaths, change):
    try:
        return update_memory(paths, change)
    except MemoryWriteError as exc:
        raise MemoryCommandError(printable(exc)) from exc


def add_line(paths: ProjectPaths, section: str, text: str) -> None:
    """Add a line of the user's own under `section` (a known section name). Raises MemoryCommandError
    with `Not added: …` when the line is refused."""
    problem = check_line(text)
    if problem is not None:
        raise MemoryCommandError(f"Not added: {problem}.")
    _update(paths, lambda doc: doc.add(Bullet(section, text.strip(), False)))


def remove_line(paths: ProjectPaths, number: int) -> str | None:
    """Remove bullet `number` (1-based, as the listing numbers them) and return its text, or None when
    there's no such bullet."""

    def take(doc: MemoryDoc) -> Bullet | None:
        try:
            return doc.remove_index(number)
        except IndexError:
            return None

    removed = _update(paths, take)
    return None if removed is None else removed.text


def running_message(run_id: str) -> str:
    return f"Run {run_id} is in progress; apply suggestions when it finishes."


def suggestion_lines(paths: ProjectPaths) -> list[str]:
    """The pending AGENTS.md suggestions, numbered; empty when there are none."""
    return [f"{n}. {printable(line)}" for n, line in enumerate(read_suggestions(paths), 1)]


def apply_lines(repo_root: Path, paths: ProjectPaths, *, create: bool) -> list[str]:
    """Apply the suggestions to AGENTS.md and return the lines to show. Raises MemoryCommandError."""
    try:
        result = apply_suggestions(repo_root, paths, create=create)
    except (ValueError, FileNotFoundError, MemoryWriteError) as exc:
        raise MemoryCommandError(printable(exc)) from exc
    lines = [f"Added {len(result.lines)} line(s) to {result.path}. Not committed: review and commit it yourself."]
    if result.memory_error:
        lines.append(f"Couldn't tidy memory: {printable(result.memory_error)}")
    return lines


def dismiss_line(paths: ProjectPaths) -> str:
    try:
        count = clear_suggestions(paths)
    except MemoryWriteError as exc:
        raise MemoryCommandError(printable(exc)) from exc
    return f"Dismissed {count} suggestion(s)."
