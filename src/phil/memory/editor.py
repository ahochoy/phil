"""Open a file in the user's editor. Typer 0.27 bundles a click without `click.edit`, so this does what
it did: `$VISUAL`, then `$EDITOR`, then `notepad` on Windows or the first of vim, vi and nano on PATH."""

import os
import shlex
import shutil
import subprocess
import sys


class EditorError(Exception):
    pass


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
    # which() finds Windows shims such as code.cmd, which CreateProcess won't find from a bare name.
    found = shutil.which(command[0])
    if found is None:
        raise EditorError(f"can't find the editor {command[0]}; set $EDITOR")
    try:
        result = subprocess.run([found, *command[1:], path], check=False)
    except OSError as exc:
        raise EditorError(f"can't start {command[0]}: {exc}") from exc
    if result.returncode != 0:
        raise EditorError(f"{command[0]} exited with status {result.returncode}")
