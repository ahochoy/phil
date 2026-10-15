"""Suggested additions to the repository's AGENTS.md (spec 2026-10-15 §2.1), kept in a file of Phil's
own for the user to apply later. One `- ` bullet per line, written under the same lock and atomic
replace as the memory file."""

import os

from phil.memory.apply import normalise
from phil.memory.store import MemoryWriteError, _locked
from phil.store.paths import ProjectPaths

_BULLET = "- "


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
