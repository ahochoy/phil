"""The system clipboard through the platform's own command (`pbcopy`, `clip.exe`, `wl-copy` or
`xclip`), for the terminals that ignore OSC 52, the escape sequence Textual copies with (macOS
Terminal.app; tmux by default)."""

import shutil
import subprocess
import sys

TIMEOUT_S = 2.0

# Module attributes, so tests can stand in for the PATH lookup and the command.
_which = shutil.which
_run = subprocess.run


def _candidates(platform: str) -> list[list[str]]:
    if platform == "darwin":
        return [["pbcopy"]]
    if platform == "win32":
        return [["clip.exe"]]
    return [["wl-copy"], ["xclip", "-selection", "clipboard"]]


def system_copy(text: str, platform: str | None = None) -> bool:
    """Put `text` on the clipboard with the first of the platform's commands on PATH that succeeds
    (run without a shell, the text on stdin); False when none is on PATH or every one failed."""
    for name, *args in _candidates(platform or sys.platform):
        path = _which(name)
        if path is None:
            continue
        # clip.exe reads UTF-16 (with its BOM) as Unicode; the others read UTF-8.
        data = text.encode("utf-16" if name == "clip.exe" else "utf-8")
        try:
            done = _run(
                [path, *args], input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return True
    return False
