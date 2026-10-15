"""Suggested additions to the repository's AGENTS.md (spec 2026-10-15 §2.1), kept in a file of Phil's
own for the user to apply later. One `- ` bullet per line, written under the same lock and atomic
replace as the memory file."""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from phil.memory.apply import check_line, normalise
from phil.memory.doc import MemoryDoc
from phil.memory.store import MemoryWriteError, _locked, update_memory
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs

NOTES_HEADING = "## Notes from Phil"
_BULLET = "- "
_IN_PROGRESS = ("pending", "running", "escalated")


def _parse(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        if raw.startswith(_BULLET) and raw[len(_BULLET):].strip():
            lines.append(raw[len(_BULLET):].strip())
    return lines


def read_suggestions(paths: ProjectPaths) -> list[str]:
    """The pending suggestions, in file order; none when the file is missing. Other lines are ignored."""
    try:
        text = paths.agents_md_suggestions_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return _parse(text)


def add_suggestions(paths: ProjectPaths, lines: list[str]) -> list[str]:
    """Append the lines not already pending (compared after normalising) and return the ones added.
    An empty list does nothing. Raises MemoryWriteError when the file can't be locked, read or written."""
    if not lines:
        return []
    path = paths.agents_md_suggestions_path
    tmp = path.with_name(path.name + ".tmp")
    try:
        with _locked(path):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                text = ""
            seen = {normalise(line) for line in _parse(text)}
            added: list[str] = []
            for line in lines:
                key = normalise(line)
                if key not in seen:
                    seen.add(key)
                    added.append(line.strip())
            if added:
                body = text if not text or text.endswith("\n") else text + "\n"
                body += "".join(f"{_BULLET}{line}\n" for line in added)
                try:
                    tmp.write_text(body, encoding="utf-8", newline="\n")
                    os.replace(tmp, path)
                except OSError:
                    tmp.unlink(missing_ok=True)
                    raise
            return added
    except OSError as exc:
        raise MemoryWriteError(f"can't write {path}: {exc}") from exc


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def clear_suggestions(paths: ProjectPaths) -> int:
    """Dismiss every pending suggestion; returns how many there were. Raises MemoryWriteError on failure."""
    path = paths.agents_md_suggestions_path
    try:
        with _locked(path):
            count = len(read_suggestions(paths))
            _remove_file(path)
            return count
    except OSError as exc:
        raise MemoryWriteError(f"can't write {path}: {exc}") from exc


@dataclass
class AppliedSuggestions:
    lines: list[str]
    created: bool
    path: Path
    removed_from_memory: int


def _target(repo_root: Path) -> Path:
    """The AGENTS.md to write: a regular file inside the repository, or the place for a new one."""
    path = repo_root / "AGENTS.md"
    if path.is_symlink():
        resolved = path.resolve()
        if not resolved.is_relative_to(repo_root.resolve()) or not resolved.is_file():
            raise ValueError("AGENTS.md is not a regular file in this repository")
        return resolved
    if path.exists() and not path.is_file():
        raise ValueError("AGENTS.md is not a regular file in this repository")
    return path


def apply_suggestions(repo_root: Path, paths: ProjectPaths, *, create: bool) -> AppliedSuggestions:
    """Append the pending suggestions to the repository's AGENTS.md under `## Notes from Phil`, drop the
    same lines from Phil's memory and clear the suggestions. The only place Phil writes AGENTS.md; the
    caller confirms with the user first. Nothing is committed. Raises FileNotFoundError when there's no
    AGENTS.md and `create` is False, ValueError when nothing valid is pending or AGENTS.md isn't a regular
    file in the repository, MemoryWriteError when a write fails."""
    sugg_path = paths.agents_md_suggestions_path
    where = sugg_path
    missing = False
    try:
        with _locked(sugg_path):
            pending = read_suggestions(paths)
            # Defence in depth: the file is Phil's own, but a line that wouldn't pass now isn't written.
            lines = [line for line in pending if check_line(line) is None]
            if not lines:
                _remove_file(sugg_path)
                raise ValueError("no suggestions")
            target = _target(repo_root)
            existed = target.exists()
            where = target
            if not existed and not create:
                missing = True
                raise FileNotFoundError(str(target))
            raw = target.read_bytes() if existed else b""
            has_heading = any(
                part.rstrip() == NOTES_HEADING for part in raw.decode("utf-8", errors="replace").splitlines()
            )
            addition = ""
            if raw and not raw.endswith(b"\n"):
                addition += "\n"
            if not has_heading:
                if raw:
                    addition += "\n"
                addition += NOTES_HEADING + "\n"
            addition += "".join(f"{_BULLET}{line}\n" for line in lines)
            tmp = target.with_name(target.name + ".phil.tmp")
            try:
                tmp.write_bytes(raw + addition.encode("utf-8"))
                os.replace(tmp, target)
            except OSError:
                tmp.unlink(missing_ok=True)
                raise
            _remove_file(sugg_path)
    except OSError as exc:
        if missing:
            raise
        raise MemoryWriteError(f"can't write {where}: {exc}") from exc

    applied = {normalise(line) for line in lines}

    def tidy(doc: MemoryDoc) -> int:
        removed = 0
        for bullet in doc.bullets():
            if bullet.by_phil and normalise(bullet.text) in applied and doc.remove(bullet.text, phil_only=True):
                removed += 1
        return removed

    try:
        removed_from_memory = update_memory(paths, tidy)
    except MemoryWriteError:
        removed_from_memory = 0  # AGENTS.md is written; a duplicate left in memory is harmless
    return AppliedSuggestions(lines, not existed, target, removed_from_memory)


def active_run(conn: sqlite3.Connection) -> str | None:
    """The id of a run still in progress (pending, running or escalated), or None."""
    for run in list_runs(conn):
        if run.state in _IN_PROGRESS:
            return run.run_id
    return None
