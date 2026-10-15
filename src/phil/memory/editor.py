"""Open a file in the user's editor. Typer 0.27 bundles a click without `click.edit`, so this does what
it did: `$VISUAL`, then `$EDITOR`, then `notepad` on Windows or the first of vim, vi and nano on PATH."""

import os
import shlex
import shutil
import subprocess
import sys


class EditorError(Exception):
    pass


def _editor_command() -> list[str]:
    for name in ("VISUAL", "EDITOR"):
        value = os.environ.get(name, "").strip()
        if value:
            try:
                parts = shlex.split(value, posix=sys.platform != "win32")
            except ValueError as exc:
                raise EditorError(f"${name} can't be read: {exc}") from exc
            if parts:
                return parts
    if sys.platform == "win32":
        return ["notepad"]
    for name in ("vim", "vi", "nano"):
        if shutil.which(name):
            return [name]
    raise EditorError("no editor found; set $EDITOR")


def edit_file(path: str) -> None:
    """Open `path` in the editor and wait for it to close. Raises EditorError when there's no usable
    editor or it exits with an error."""
    command = _editor_command()
    try:
        result = subprocess.run([*command, path], check=False)
    except OSError as exc:
        raise EditorError(f"can't start {command[0]}: {exc}") from exc
    if result.returncode != 0:
        raise EditorError(f"{command[0]} exited with status {result.returncode}")
