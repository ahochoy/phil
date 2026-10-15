"""Open a file in the user's editor. Typer 0.27 bundles a click without `click.edit`, so this does what
it did: `$VISUAL`, then `$EDITOR`, then `notepad` on Windows or the first of vim, vi and nano on PATH."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


_BATCH_UNSAFE = frozenset('"&|<>^%!\n\r')


class EditorError(Exception):
    pass


def _inside_cwd(found: str) -> bool:
    return Path(found).resolve().is_relative_to(Path.cwd().resolve())


def _is_windows() -> bool:
    return sys.platform == "win32"


def _split(value: str) -> list[str]:
    """Split an editor setting into words. On Windows backslashes stay literal, and one pair of double
    quotes around a word is removed (shlex leaves them in when not in POSIX mode)."""
    if not _is_windows():
        return shlex.split(value)
    parts = shlex.split(value, posix=False)
    return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part for part in parts]


def _editor_command() -> list[str]:
    for name in ("VISUAL", "EDITOR"):
        value = os.environ.get(name, "").strip()
        if value:
            try:
                parts = _split(value)
            except ValueError as exc:
                raise EditorError(f"${name} can't be read: {exc}") from exc
            if parts:
                return parts
    if _is_windows():
        return ["notepad"]
    for name in ("vim", "vi", "nano"):
        if shutil.which(name):
            return [name]
    raise EditorError("no editor found; set $EDITOR")


def edit_file(path: str) -> None:
    """Open `path` in the editor and wait for it to close. Raises EditorError when there's no usable
    editor or it exits with an error."""
    command = _editor_command()
    name = command[0]
    explicit = os.path.isabs(name)  # a full path is the user's choice, allowed anywhere
    # which() finds Windows shims such as code.cmd, which CreateProcess won't find from a bare name.
    if _is_windows() and not os.path.dirname(name):
        # Windows searches the current folder first, and that's the repo: look on PATH only.
        search = os.pathsep.join(
            entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry.strip() not in ("", ".")
        )
        found = shutil.which(name, path=search)
    else:
        found = shutil.which(name)
    if found is None:
        raise EditorError(f"can't find the editor {name}; set $EDITOR")
    if not explicit and _inside_cwd(found):
        raise EditorError(f"won't start {name} from the current folder; set $EDITOR to a full path")
    if _is_windows() and found.lower().endswith((".cmd", ".bat")):
        # cmd.exe re-parses the arguments, and Python's quoting doesn't stop its metacharacters.
        if any(char in arg for arg in (*command[1:], path) for char in _BATCH_UNSAFE):
            raise EditorError(
                f"won't start {name} (a batch file) with this path; set $EDITOR to the editor's .exe"
            )
    try:
        result = subprocess.run([found, *command[1:], path], check=False)
    except OSError as exc:
        raise EditorError(f"can't start {command[0]}: {exc}") from exc
    if result.returncode != 0:
        raise EditorError(f"{command[0]} exited with status {result.returncode}")
