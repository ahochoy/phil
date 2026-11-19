"""The system clipboard through the platform's own command (`pbcopy`, `clip.exe`, `wl-copy` or
`xclip`), for the terminals that ignore OSC 52, the escape sequence Textual copies with (macOS
Terminal.app; tmux by default).

Phil runs inside arbitrary repositories, so a command is never taken from the current directory or
a repository: on Windows only `%SystemRoot%\\System32\\clip.exe`; elsewhere a PATH lookup over its
absolute entries only, and never a program under the current directory or a distrusted root (the
chat's repository, `distrust`)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 2.0

# Module attributes, so tests can stand in for the PATH lookup, the file check and the command.
_which = shutil.which
_isfile = os.path.isfile
_run = subprocess.run

_distrusted: list[Path] = []


def distrust(root: Path) -> None:
    """Never run a clipboard command from under `root` (the repository the chat works in)."""
    _distrusted.append(Path(root))


def _windows_clip() -> str | None:
    path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "clip.exe")
    return path if _isfile(path) else None


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root.resolve())
    except OSError:
        return True  # can't tell: don't run it


def _resolve(name: str) -> str | None:
    """`name` on PATH's absolute entries, unless it lies under the current directory or a
    distrusted root."""
    search = os.pathsep.join(
        entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry and os.path.isabs(entry)
    )
    found = _which(name, path=search)
    if found is None:
        return None
    try:
        resolved = Path(found).resolve()
        roots = [Path.cwd(), *_distrusted]
    except OSError:
        return None
    if any(_inside(resolved, root) for root in roots):
        return None
    return found


def _commands(platform: str) -> list[list[str]]:
    if platform == "win32":
        clip = _windows_clip()
        return [[clip]] if clip else []
    names = [["pbcopy"]] if platform == "darwin" else [["wl-copy"], ["xclip", "-selection", "clipboard"]]
    found = []
    for name, *args in names:
        path = _resolve(name)
        if path is not None:
            found.append([path, *args])
    return found


def system_copy(text: str, platform: str | None = None) -> bool:
    """Put `text` on the clipboard with the first of the platform's commands that succeeds (run
    without a shell, the text on stdin); False when there is none or every one failed."""
    platform = platform or sys.platform
    for command in _commands(platform):
        # clip.exe reads UTF-16 (with its BOM) as Unicode; the others read UTF-8.
        data = text.encode("utf-16" if platform == "win32" else "utf-8")
        try:
            done = _run(
                command, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return True
    return False
