"""Where Phil's diagnostics go.

For people using Phil, library warnings, other libraries' log lines and crash tracebacks go to log
files, never the terminal; things a user must act on are said in plain words by the command itself.
With `PHIL_DEBUG=1`, Phil's developer also sees them on stderr.
"""

import logging
import os
import platform
import sys
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from phil import __version__
from phil.store.paths import phil_home

DEBUG_ENV = "PHIL_DEBUG"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3
CRASH_KEEP = 20
_FORMAT = "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"

# What `configure` installed, so a second call or `reset` can take it out again.
_state: dict = {"handlers": [], "file": None, "level": None, "raise_exceptions": True, "hooks": None}


def debug_enabled() -> bool:
    return os.environ.get(DEBUG_ENV, "").strip().lower() in {"1", "true", "yes"}


class _Levels(logging.Filter):
    """Phil's own records from `phil_level` up, every other library's from `other_level` up."""

    def __init__(self, phil_level: int, other_level: int) -> None:
        super().__init__()
        self.phil_level, self.other_level = phil_level, other_level

    def filter(self, record: logging.LogRecord) -> bool:
        is_phil = record.name == "phil" or record.name.startswith("phil.")
        return record.levelno >= (self.phil_level if is_phil else self.other_level)


class _FileHandler(logging.FileHandler):
    """A `FileHandler` whose failure to open its file is handled like any other write failure.

    The plain one opens the file inside `emit` unguarded, so an unwritable chat log would raise
    into whatever code was logging.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except Exception:
            self.handleError(record)


def _thread_hook(args) -> None:
    """Log a thread's uncaught exception instead of printing it above the prompt."""
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    logging.getLogger("phil.diagnostics").error(
        "uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )


def _unraisable_hook(unraisable) -> None:
    """Log an exception Python can't raise (a finalizer's, a callback's) instead of printing it."""
    try:
        where = repr(unraisable.object)
    except Exception:
        where = "<unprintable object>"
    logging.getLogger("phil.diagnostics").error(
        "%s: %s",
        unraisable.err_msg or "Exception ignored in",
        where,
        exc_info=(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
    )


def _file_handler(path: Path, *, rotate: bool) -> logging.Handler:
    """A file handler on `path`, or a `NullHandler` when its directory can't be made."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if rotate:
            handler: logging.Handler = RotatingFileHandler(
                path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True
            )
        else:
            handler = _FileHandler(path, encoding="utf-8", delay=True)
    except OSError:
        return logging.NullHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(_Levels(logging.INFO, logging.WARNING))
    return handler


def configured() -> bool:
    return bool(_state["handlers"])


def configure(log_path: Path) -> None:
    """Send every logger and every Python warning to `log_path`, and to stderr too in debug mode.

    Safe to call again: the second call replaces what the first installed.
    """
    reset()
    root = logging.getLogger()
    debug = debug_enabled()
    file = _file_handler(log_path, rotate=True)
    handlers = [file]
    if debug:
        stderr = logging.StreamHandler(sys.stderr)
        stderr.setFormatter(logging.Formatter(_FORMAT))
        stderr.addFilter(_Levels(logging.DEBUG, logging.WARNING))
        handlers.append(stderr)
    _state.update(
        handlers=handlers,
        file=file,
        level=root.level,
        raise_exceptions=logging.raiseExceptions,
        hooks=(threading.excepthook, sys.unraisablehook),
    )
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # A handler that fails to write (a full disk, a read-only file) would otherwise print
    # "--- Logging error ---" and a traceback to stderr.
    logging.raiseExceptions = debug
    logging.captureWarnings(True)
    threading.excepthook = _thread_hook
    sys.unraisablehook = _unraisable_hook


def reset() -> None:
    """Undo `configure` (tests use this; `configure` calls it so a second call replaces the first)."""
    if not _state["handlers"]:
        return
    root = logging.getLogger()
    for handler in _state["handlers"]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(_state["level"])
    logging.raiseExceptions = _state["raise_exceptions"]
    logging.captureWarnings(False)
    threading.excepthook, sys.unraisablehook = _state["hooks"]
    _state.update(handlers=[], file=None)


@contextmanager
def redirect(log_path: Path) -> Iterator[None]:
    """Send the file diagnostics to `log_path` (a chat's or a run's own log) while active."""
    root = logging.getLogger()
    previous = _state["file"]
    handler = _file_handler(log_path, rotate=False)
    # Add before removing, both ways: a moment with no handler would send a watcher thread's
    # warning to Python's last-resort handler, on stderr.
    root.addHandler(handler)
    if previous is not None:
        root.removeHandler(previous)
        _state["handlers"] = [handler if h is previous else h for h in _state["handlers"]]
        _state["file"] = handler
    try:
        yield
    finally:
        if previous is not None:
            root.addHandler(previous)
            _state["handlers"] = [previous if h is handler else h for h in _state["handlers"]]
            _state["file"] = previous
        root.removeHandler(handler)
        handler.close()


def crash(exc: BaseException, command: str | None) -> Path | None:
    """Save `exc`'s traceback to a new crash file and return its path, or None if it can't be written.

    The file names the subcommand, never argv's values, which can hold a key.
    """
    directory = phil_home() / "logs"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"crash-{stamp}-{os.getpid()}.log"
    header = [
        f"Phil: {__version__}",
        f"Python: {sys.version.split()[0]}",
        f"OS: {platform.platform()}",
        f"Command: {command or '(none)'}",
        "",
    ]
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(header) + "".join(traceback.format_exception(exc)), encoding="utf-8")
    except OSError:
        return None
    for old in sorted(directory.glob("crash-*.log"))[:-CRASH_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def crash_message(exc: BaseException, path: Path | None) -> str:
    name = type(exc).__name__
    if path is None:
        return f"Phil hit an unexpected error ({name})."
    return f"Phil hit an unexpected error ({name}). Details were saved to {path} — please include it if you report this."
