"""Which chat a terminal gets: the full-screen frame, the inline chat, or plain lines.

Imports nothing from Textual, so the CLI can decide before loading the frame."""

import os
from collections.abc import Mapping

MIN_COLUMNS, MIN_ROWS = 60, 15
TOO_SMALL = f"This terminal is smaller than {MIN_COLUMNS}×{MIN_ROWS}; using the inline chat."
LEGACY_CONSOLE = "This console can't show the full-screen chat; using the inline chat."


def windows_console_ok(is_windows: bool, environ: Mapping[str, str] = os.environ) -> bool:
    """True off Windows; on Windows only in a terminal known to draw the frame (Windows Terminal
    sets `WT_SESSION`, VS Code sets `TERM_PROGRAM`), not the legacy console."""
    return not is_windows or bool(environ.get("WT_SESSION") or environ.get("TERM_PROGRAM"))


def choose_mode(setting: str, tty: bool, columns: int, rows: int, windows_console_ok: bool) -> tuple[str, str | None]:
    """`("full" | "inline" | "line", note)`: plain lines without a terminal; else the setting, with
    a one-line note when the full screen was asked for but this terminal can't have it."""
    if not tty:
        return "line", None
    if setting == "inline":
        return "inline", None
    if columns < MIN_COLUMNS or rows < MIN_ROWS:
        return "inline", TOO_SMALL
    if not windows_console_ok:
        return "inline", LEGACY_CONSOLE
    return "full", None
