# Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Library warnings, other libraries' log lines and crash tracebacks never reach a user's terminal; they go to log files, `PHIL_DEBUG=1` shows them again, and the tests fail on warnings Phil's own code raises.

**Architecture:** A new `phil.diagnostics` module owns the root logger's handlers, warning capture and crash files. A new console entry point, `phil.cli.entry:main`, sets it up before the CLI is imported and turns any uncaught exception into one line plus a crash file. The chat and the worker point the file handler at their own logs with `diagnostics.redirect`.

**Tech Stack:** Python 3.14, the standard `logging`/`warnings`/`traceback` modules, Typer 0.27 (which vendors Click as `typer._click`; there is no `click` package), pytest with xdist.

**Spec:** `docs/superpowers/specs/2026-10-20-phil-diagnostics-design.md`

## Global Constraints

- The switch is the environment variable `PHIL_DEBUG`; `1`, `true` and `yes` (any case, surrounding spaces ignored) turn it on; anything else, including `0` and `false`, leaves it off. No `--debug` flag.
- The user's crash line, exactly: `Phil hit an unexpected error (<ExceptionType>). Details were saved to <path> — please include it if you report this.` Without a path: `Phil hit an unexpected error (<ExceptionType>).` Exit code 1.
- Command logs: `<phil home>/logs/phil.log`, rotating at 1 MB with 3 backups. Crash files: `<phil home>/logs/crash-<UTC timestamp>-<pid>.log`, newest 20 kept. `<phil home>` is `phil.store.paths.phil_home()`.
- A chat's diagnostics go to its own `phil.log`; a worker's to `<run dir>/logs/worker.log`.
- File handlers record `phil.*` from `INFO` and every other logger from `WARNING`. The debug stderr handler shows `phil.*` from `DEBUG` and every other logger from `WARNING`.
- A crash file holds the traceback (never local variables), Phil's version, Python's version, the OS and the subcommand names only — never argv's option or argument values.
- Local variables are never printed, debug mode included. The Typer app is created with `pretty_exceptions_enable=False`.
- `KeyboardInterrupt` exits 130 with no crash file and no output.
- `[tool.pytest.ini_options] filterwarnings = ["error:::phil"]`; warnings it exposes are fixed at their cause.
- Deliberate user-facing output (`[phil.error]` lines, callouts, normal command output) is unchanged.
- Repository rules: edit files only with Edit/Write; commit with the message written to `.git/phil-commit-msg.txt` and `git commit -F .git/phil-commit-msg.txt`; trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; stage only this plan's files; tests run with `PHIL_HOME` in a temp dir (the root conftest already does this); never run `-m live` or `-m bench`.

## Review Focus

1. **A crash line on a stderr that can't encode `—`** (a Windows pipe in cp1252): the user gets the line with the dash replaced, never a `UnicodeEncodeError` traceback. Pinned in Task 2 (`test_the_crash_line_survives_a_stderr_that_cannot_encode_it`).
2. **A log file that fails at write time** (the path is a directory, a read-only file): nothing on stderr, no `--- Logging error ---` block. Pinned in Task 1 (`test_a_log_that_cannot_be_written_stays_silent`).
3. **A crash before the CLI loads** (a broken install, an import error in `phil.cli.main`): still the one line and a crash file. Pinned in Task 2 (`test_a_crash_while_loading_the_cli_is_one_line`).
4. **A key typed on the command line** (`phil keys set OPENROUTER sk-…` then a crash): the crash file names `keys set` and never the key. Pinned in Task 2 (`test_the_command_path_keeps_only_subcommand_names`).
5. **`PHIL_DEBUG=0` or `PHIL_DEBUG=false`**: debug stays off. Pinned in Task 1 (`test_debug_is_on_only_for_yes_values`).

## Rulings carried from planning

- The spec says Typer's pretty tracebacks show local variables by default. In Typer 0.27 `pretty_exceptions_show_locals` defaults to `False`. Turning pretty exceptions off is still right: `entry.run` must see the raw exception, and debug mode shows Python's plain traceback.
- Typer already turns a `KeyboardInterrupt` inside a command into `SystemExit(130)` (checked with Typer 0.27.2). `entry.run` adds the same for one raised outside a command, such as during the import.
- `run` takes a loader (`Callable[[], typer.Typer]`) instead of the app, so an exception while importing `phil.cli.main` is handled like any other crash.
- The crash file keeps the exception's own message, which a developer needs; it doesn't record argv separately.
- `redirect` uses a plain `FileHandler`, not a rotating one, matching today's per-chat log; rotating a file the worker's stdout also holds open would break on Windows.
- `CONTRIBUTING.md` doesn't exist on `main` yet (it arrives with PR #50), so the docs go in the README's Development section; whichever PR merges second moves them.

---

## File Structure

- Create `src/phil/diagnostics.py`: debug switch, `configure`/`reset`/`configured`, `redirect`, `crash`, `crash_message`.
- Create `src/phil/cli/entry.py`: `main`, `run`, `command_path`, `_print_err`.
- Modify `src/phil/__main__.py`, `pyproject.toml` (`[project.scripts]`, `filterwarnings`), `src/phil/cli/main.py` (Typer app setting, chat and worker use `redirect`), `src/phil/chat/session.py` (remove `chat_logging`).
- Tests: create `tests/test_diagnostics.py`, `tests/cli/test_entry.py`, `tests/cli/test_worker_diagnostics.py`; modify `tests/chat/test_watcher.py`.
- Docs: `README.md` (Development), `docs/superpowers/roadmap.md`.

---

### Task 1: `phil.diagnostics`

**Files:**
- Create: `src/phil/diagnostics.py`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `phil.store.paths.phil_home() -> Path`, `phil.__version__: str`.
- Produces:
  - `DEBUG_ENV = "PHIL_DEBUG"`, `CRASH_KEEP = 20`
  - `debug_enabled() -> bool`
  - `configure(log_path: Path) -> None`, `configured() -> bool`, `reset() -> None`
  - `redirect(log_path: Path) -> ContextManager[None]`
  - `crash(exc: BaseException, command: str | None) -> Path | None`
  - `crash_message(exc: BaseException, path: Path | None) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_diagnostics.py`:

```python
import logging
import sys
import warnings
from pathlib import Path

import pytest

from phil import diagnostics


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("PHIL_DEBUG", raising=False)
    yield
    diagnostics.reset()


@pytest.mark.parametrize(
    ("value", "on"),
    [("1", True), ("true", True), (" YES ", True), ("0", False), ("false", False), ("", False), ("no", False)],
)
def test_debug_is_on_only_for_yes_values(monkeypatch, value, on):
    monkeypatch.setenv("PHIL_DEBUG", value)
    assert diagnostics.debug_enabled() is on


def test_a_library_log_line_goes_to_the_file_not_the_terminal(tmp_path, capfd):
    diagnostics.configure(tmp_path / "logs" / "phil.log")
    logging.getLogger("somelib").warning("a library log line")
    logging.getLogger("somelib").info("library chatter")
    logging.getLogger("phil.x").info("phil detail")
    out, err = capfd.readouterr()
    assert (out, err) == ("", "")
    log = (tmp_path / "logs" / "phil.log").read_text(encoding="utf-8")
    assert "a library log line" in log and "phil detail" in log
    assert "library chatter" not in log


def test_a_warning_becomes_a_log_record(tmp_path, capfd):
    diagnostics.configure(tmp_path / "phil.log")
    warnings.warn("a library warning", UserWarning)  # attributed to this test module, not to phil
    assert capfd.readouterr().err == ""
    assert "a library warning" in (tmp_path / "phil.log").read_text(encoding="utf-8")


def test_debug_mirrors_to_stderr(tmp_path, capfd, monkeypatch):
    monkeypatch.setenv("PHIL_DEBUG", "1")
    diagnostics.configure(tmp_path / "phil.log")
    logging.getLogger("phil.x").debug("phil debug detail")
    logging.getLogger("somelib").info("library chatter")
    logging.getLogger("somelib").warning("a library log line")
    err = capfd.readouterr().err
    assert "phil debug detail" in err and "a library log line" in err
    assert "library chatter" not in err


def test_configure_twice_leaves_one_file_handler(tmp_path):
    before = list(logging.getLogger().handlers)
    diagnostics.configure(tmp_path / "a.log")
    diagnostics.configure(tmp_path / "b.log")
    added = [h for h in logging.getLogger().handlers if h not in before]
    assert len(added) == 1
    assert diagnostics.configured()
    diagnostics.reset()
    assert logging.getLogger().handlers == before
    assert not diagnostics.configured()


def test_redirect_switches_files_and_back(tmp_path):
    diagnostics.configure(tmp_path / "phil.log")
    log = logging.getLogger("somelib")
    with diagnostics.redirect(tmp_path / "chat" / "phil.log"):
        log.warning("inside the chat")
    log.warning("after the chat")
    chat = (tmp_path / "chat" / "phil.log").read_text(encoding="utf-8")
    main = (tmp_path / "phil.log").read_text(encoding="utf-8")
    assert "inside the chat" in chat and "after the chat" not in chat
    assert "after the chat" in main and "inside the chat" not in main


def test_redirect_works_without_configure(tmp_path, capfd):
    with diagnostics.redirect(tmp_path / "chat" / "phil.log"):
        logging.getLogger("phil.watch").warning("watcher failed")
    assert capfd.readouterr().err == ""
    assert "watcher failed" in (tmp_path / "chat" / "phil.log").read_text(encoding="utf-8")


def test_an_unusable_log_directory_stays_silent(tmp_path, capfd):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    diagnostics.configure(blocker / "logs" / "phil.log")
    logging.getLogger("somelib").warning("nowhere to go")
    assert capfd.readouterr().err == ""


def test_a_log_that_cannot_be_written_stays_silent(tmp_path, capfd):
    (tmp_path / "phil.log").mkdir()  # opening it for append fails at the first write
    diagnostics.configure(tmp_path / "phil.log")
    logging.getLogger("somelib").warning("nowhere to go")
    assert capfd.readouterr().err == ""


def test_crash_writes_a_file_without_locals(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path))

    def explode():
        planted_secret = "sk-planted-123"  # noqa: F841 - must not reach the file
        raise RuntimeError("it broke")

    try:
        explode()
    except RuntimeError as exc:
        path = diagnostics.crash(exc, "keys set")
    assert path is not None and path.parent == tmp_path / "logs"
    assert path.name.startswith("crash-") and path.suffix == ".log"
    text = path.read_text(encoding="utf-8")
    assert "RuntimeError: it broke" in text and "Command: keys set" in text
    assert "Phil:" in text and "Python:" in text and "OS:" in text
    assert "sk-planted-123" not in text


def test_crash_keeps_the_newest_twenty(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path))
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(25):
        (logs / f"crash-20260101T0000{i:02d}000000Z-1.log").write_text("old")
    path = diagnostics.crash(RuntimeError("new"), None)
    kept = sorted(p.name for p in logs.glob("crash-*.log"))
    assert len(kept) == diagnostics.CRASH_KEEP
    assert path.name in kept
    assert "crash-20260101T000000000000Z-1.log" not in kept


def test_crash_returns_none_when_it_cannot_write(tmp_path, monkeypatch):
    blocker = tmp_path / "home"
    blocker.write_text("x")
    monkeypatch.setenv("PHIL_HOME", str(blocker))
    assert diagnostics.crash(RuntimeError("x"), None) is None


def test_crash_message():
    exc = KeyError("sk-secret")
    assert diagnostics.crash_message(exc, Path("/h/logs/crash-1.log")) == (
        "Phil hit an unexpected error (KeyError). Details were saved to /h/logs/crash-1.log"
        " — please include it if you report this."
    )
    assert diagnostics.crash_message(exc, None) == "Phil hit an unexpected error (KeyError)."
```

Note on `test_a_warning_becomes_a_log_record`: pytest records warnings per test, but `captureWarnings(True)` replaces `warnings.showwarning`, and a replaced `showwarning` takes precedence over pytest's recorder, so the warning reaches logging. If pytest swallows it anyway (the log stays empty), ledger a ruling and move this check to the subprocess tests in Task 2, which already cover it; don't weaken the assertion.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_diagnostics.py -n 0 -q`
Expected: collection error, `ImportError: cannot import name 'diagnostics' from 'phil'`.

- [ ] **Step 3: Write the module**

`src/phil/diagnostics.py`:

```python
"""Where Phil's diagnostics go.

For people using Phil, library warnings, other libraries' log lines and crash tracebacks go to log
files, never the terminal; things a user must act on are said in plain words by the command itself.
With `PHIL_DEBUG=1`, Phil's developer also sees them on stderr.
"""

import logging
import os
import platform
import sys
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
_state: dict = {"handlers": [], "file": None, "level": None, "raise_exceptions": True}


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


def _file_handler(path: Path, *, rotate: bool) -> logging.Handler:
    """A file handler on `path`, or a `NullHandler` when its directory can't be made."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if rotate:
            handler: logging.Handler = RotatingFileHandler(
                path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True
            )
        else:
            handler = logging.FileHandler(path, encoding="utf-8", delay=True)
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
    _state.update(handlers=handlers, file=file, level=root.level, raise_exceptions=logging.raiseExceptions)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # A handler that fails to write (a full disk, a read-only file) would otherwise print
    # "--- Logging error ---" and a traceback to stderr.
    logging.raiseExceptions = debug
    logging.captureWarnings(True)


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
    _state.update(handlers=[], file=None)


@contextmanager
def redirect(log_path: Path) -> Iterator[None]:
    """Send the file diagnostics to `log_path` (a chat's or a run's own log) while active."""
    root = logging.getLogger()
    previous = _state["file"]
    handler = _file_handler(log_path, rotate=False)
    if previous is not None:
        root.removeHandler(previous)
        _state["handlers"] = [handler if h is previous else h for h in _state["handlers"]]
        _state["file"] = handler
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
        if previous is not None:
            root.addHandler(previous)
            _state["handlers"] = [previous if h is handler else h for h in _state["handlers"]]
            _state["file"] = previous


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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_diagnostics.py -n 0 -q`
Expected: all pass (19 tests with the parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add src/phil/diagnostics.py tests/test_diagnostics.py
# .git/phil-commit-msg.txt:
#   Diagnostics: log files for users, stderr too with PHIL_DEBUG=1
#
#   (body: what configure/redirect/crash do; trailer)
git commit -F .git/phil-commit-msg.txt
```

---

### Task 2: Entry point and crash handling

**Files:**
- Create: `src/phil/cli/entry.py`
- Modify: `src/phil/__main__.py`, `pyproject.toml` (`[project.scripts]`), `src/phil/cli/main.py:56` (the `typer.Typer(...)` call)
- Test: `tests/cli/test_entry.py`

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces: `phil.cli.entry.main() -> None`, `run(load_app: Callable[[], typer.Typer]) -> None`, `command_path(app: typer.Typer, argv: Sequence[str]) -> str | None`, `_print_err(text: str, stream: TextIO | None = None) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/cli/test_entry.py`:

```python
import io
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import typer

import phil
from phil.cli.entry import _print_err, command_path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import logging, sys, warnings
import typer
from phil.cli.entry import run

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)

@app.command()
def warn():
    warnings.warn("a library warning", UserWarning)

@app.command()
def log():
    logging.getLogger("somelib").warning("a library log line")

@app.command()
def boom():
    planted_secret = "sk-planted-123"
    raise RuntimeError("it broke")

@app.command()
def interrupt():
    raise KeyboardInterrupt

def broken_loader():
    raise ImportError("a broken install")

run(broken_loader if sys.argv[1:] == ["load-fails"] else (lambda: app))
"""


def launch(home, *args, debug=False):
    env = {k: v for k, v in os.environ.items() if k != "PHIL_DEBUG"}
    env["PHIL_HOME"] = str(home)
    if debug:
        env["PHIL_DEBUG"] = "1"
    return subprocess.run(
        [sys.executable, "-I", "-c", SCRIPT, *args], env=env, capture_output=True, text=True, encoding="utf-8"
    )


def crash_files(home):
    return sorted((home / "logs").glob("crash-*.log"))


def test_a_library_warning_stays_off_the_terminal(tmp_path):
    result = launch(tmp_path, "warn")
    assert result.returncode == 0 and result.stderr == ""
    assert "a library warning" in (tmp_path / "logs" / "phil.log").read_text(encoding="utf-8")


def test_a_library_log_line_stays_off_the_terminal(tmp_path):
    result = launch(tmp_path, "log")
    assert result.returncode == 0 and result.stderr == ""
    assert "a library log line" in (tmp_path / "logs" / "phil.log").read_text(encoding="utf-8")


def test_debug_shows_warnings_and_log_lines(tmp_path):
    assert "a library warning" in launch(tmp_path, "warn", debug=True).stderr
    assert "a library log line" in launch(tmp_path, "log", debug=True).stderr


def test_a_crash_is_one_line_and_a_file(tmp_path):
    result = launch(tmp_path, "boom")
    [path] = crash_files(tmp_path)
    assert result.returncode == 1
    assert result.stderr == (
        f"Phil hit an unexpected error (RuntimeError). Details were saved to {path}"
        " — please include it if you report this.\n"
    )
    text = path.read_text(encoding="utf-8")
    assert "RuntimeError: it broke" in text and "Command: boom" in text
    assert "sk-planted-123" not in text


def test_debug_shows_the_traceback_without_locals(tmp_path):
    result = launch(tmp_path, "boom", debug=True)
    assert result.returncode == 1
    assert "Traceback" in result.stderr and "RuntimeError: it broke" in result.stderr
    assert "sk-planted-123" not in result.stderr
    assert crash_files(tmp_path) == []


def test_an_interrupt_exits_quietly(tmp_path):
    result = launch(tmp_path, "interrupt")
    assert result.returncode == 130 and result.stderr == ""
    assert crash_files(tmp_path) == []


def test_a_crash_while_loading_the_cli_is_one_line(tmp_path):
    result = launch(tmp_path, "load-fails")
    [path] = crash_files(tmp_path)
    assert result.returncode == 1
    assert result.stderr.startswith("Phil hit an unexpected error (ImportError). Details were saved to ")
    assert "Command: (none)" in path.read_text(encoding="utf-8")


def test_the_command_path_keeps_only_subcommand_names():
    app = typer.Typer()
    keys = typer.Typer()
    app.add_typer(keys, name="keys")

    @keys.command("set")
    def set_key(name: str, value: str) -> None: ...

    @app.command()
    def runs() -> None: ...

    argv = ["--repo", "/x", "keys", "set", "OPENROUTER", "sk-abc"]
    assert command_path(app, argv) == "keys set"
    assert command_path(app, ["--version"]) is None


def test_the_crash_line_survives_a_stderr_that_cannot_encode_it():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    _print_err("Phil hit an unexpected error (KeyError). Details — x → y", stream)
    stream.flush()
    assert raw.getvalue().decode("cp1252").startswith("Phil hit an unexpected error (KeyError).")


def test_the_console_script_is_the_entry_point():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["phil"] == "phil.cli.entry:main"


def test_python_m_phil_still_prints_the_version(tmp_path):
    env = {**os.environ, "PHIL_HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "phil", "--version"], env=env, capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0 and result.stdout.strip() == phil.__version__
    assert result.stderr == ""
```

(`→` is outside cp1252, so `_print_err` must fall back; `—` is inside it.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/cli/test_entry.py -n 0 -q`
Expected: collection error, `ModuleNotFoundError: No module named 'phil.cli.entry'`.

- [ ] **Step 3: Write the entry point**

`src/phil/cli/entry.py`:

```python
"""Phil's console entry point: diagnostics first, then the CLI.

Nothing heavy is imported at module level, so warnings raised while the CLI's libraries import
are already captured.
"""

import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from phil import diagnostics
from phil.store.paths import phil_home


def _log_path():
    return phil_home() / "logs" / "phil.log"


def main() -> None:
    diagnostics.configure(_log_path())

    def load():
        from phil.cli.main import app

        return app

    run(load)


def run(load_app: Callable) -> None:
    """Load and run the CLI; an uncaught exception becomes one line and a crash file.

    With PHIL_DEBUG=1 the exception is re-raised instead, for Python's own traceback.
    """
    if not diagnostics.configured():
        diagnostics.configure(_log_path())
    app = None
    try:
        app = load_app()
        app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        if diagnostics.debug_enabled():
            raise
        command = command_path(app, sys.argv[1:]) if app is not None else None
        _print_err(diagnostics.crash_message(exc, diagnostics.crash(exc, command)))
        raise SystemExit(1) from None


def command_path(app, argv: Sequence[str]) -> str | None:
    """The subcommand names in `argv` (`keys set`), never option or argument values."""
    import typer.main

    try:
        group = typer.main.get_command(app)
    except Exception:
        return None
    names = []
    for token in argv:
        commands = getattr(group, "commands", None)
        if not commands:
            break
        if token in commands:
            names.append(token)
            group = commands[token]
    return " ".join(names) or None


def _print_err(text: str, stream: TextIO | None = None) -> None:
    """Print to stderr, replacing what its encoding can't show rather than failing."""
    stream = stream if stream is not None else sys.stderr
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding), file=stream)
```

`src/phil/__main__.py`:

```python
from phil.cli.entry import main

if __name__ == "__main__":
    main()
```

`pyproject.toml`, `[project.scripts]`:

```toml
phil = "phil.cli.entry:main"
```

`src/phil/cli/main.py:56`:

```python
app = typer.Typer(add_completion=False, pretty_exceptions_enable=False, help="Phil: a contract-driven coding agent.")
```

After changing `[project.scripts]`, run `uv sync` so the venv's `phil` script points at the new entry.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/cli/test_entry.py tests/test_cli.py -n 0 -q`
Expected: all pass. If a partial `print` wrote some bytes before the `UnicodeEncodeError` in the cp1252 test, the `startswith` assertion still holds because the fallback prints the full line after; if it doesn't, ledger a ruling and make `_print_err` encode first, then write.

- [ ] **Step 5: Commit**

```bash
git add src/phil/cli/entry.py src/phil/__main__.py pyproject.toml uv.lock src/phil/cli/main.py tests/cli/test_entry.py
# message: "Entry point: a crash is one line and a file; diagnostics set up first"
git commit -F .git/phil-commit-msg.txt
```

(Stage `uv.lock` only if `uv sync` changed it.)

---

### Task 3: The chat and the worker log through `redirect`

**Files:**
- Modify: `src/phil/chat/session.py:78-97` (remove `chat_logging`; keep `LOG_FILE`), `src/phil/cli/main.py` (`_chat` import at line ~345 and the `with chat_logging(session.dir):` at ~428; the `_worker` command at ~836)
- Modify: `tests/chat/test_watcher.py:239-258`
- Test: `tests/cli/test_worker_diagnostics.py`

**Interfaces:**
- Consumes: `diagnostics.redirect(log_path: Path)`; `phil.chat.session.LOG_FILE = "phil.log"`; `ProjectPaths(slug).run_dir(run_id)`; `resolve_repo(start) -> RepoInfo` (`.slug`), `RepoError`.
- Produces: `phil.cli.main._worker_log(start: Path, run_id: str) -> Path | None`.

- [ ] **Step 1: Write the failing tests**

Replace `test_chat_logging_keeps_watcher_tracebacks_off_the_terminal` in `tests/chat/test_watcher.py` with:

```python
def test_the_chat_log_keeps_watcher_tracebacks_off_the_terminal(calc_repo, tmp_path, monkeypatch, capfd):
    import threading

    from phil import diagnostics
    from phil.chat.session import LOG_FILE

    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(watcher, "poll_once", boom)
    capfd.readouterr()
    with diagnostics.redirect(tmp_path / "chat" / LOG_FILE):
        thread = threading.Thread(target=watcher._tick)
        thread.start()
        thread.join(5)
        logging.getLogger("httpx").warning("a library log line")
    assert capfd.readouterr().err == ""
    log = (tmp_path / "chat" / LOG_FILE).read_text(encoding="utf-8")
    assert "run watcher poll failed" in log and "RuntimeError: boom" in log
    assert "a library log line" in log


def test_chat_logging_is_gone():
    import phil.chat.session as session

    assert not hasattr(session, "chat_logging")
```

`tests/cli/test_worker_diagnostics.py`:

```python
import logging
from types import SimpleNamespace

from typer.testing import CliRunner

import phil.run.worker as worker_module
from phil.cli.main import _worker_log, app
from phil.repo import resolve_repo
from phil.store.paths import ProjectPaths

runner = CliRunner()


def test_a_worker_logs_into_its_runs_worker_log(git_repo, monkeypatch):
    def fake_run_worker(start, run_id, mode, decision, factory=None):
        logging.getLogger("httpx").warning("a library log line in the worker")
        return SimpleNamespace(status="done")

    monkeypatch.setattr(worker_module, "run_worker", fake_run_worker)
    result = runner.invoke(app, ["--repo", str(git_repo), "_worker", "r-test", "--mode", "start"])
    assert result.exit_code == 0, result.output
    log = ProjectPaths(resolve_repo(git_repo).slug).run_dir("r-test") / "logs" / "worker.log"
    assert "a library log line in the worker" in log.read_text(encoding="utf-8")
    assert "a library log line" not in result.output


def test_the_worker_log_outside_a_repository_is_none(tmp_path):
    assert _worker_log(tmp_path, "r-test") is None
```

(Check `run_worker`'s real signature in `src/phil/run/worker.py` before running; match the fake's parameters to it.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/chat/test_watcher.py tests/cli/test_worker_diagnostics.py -n 0 -q`
Expected: `test_chat_logging_is_gone` fails (`chat_logging` still exists); the worker tests fail with `ImportError: cannot import name '_worker_log'`.

- [ ] **Step 3: Implement**

`src/phil/chat/session.py`: delete `chat_logging` (lines 78–97) and any import it alone used (`contextmanager`, `Iterator`, `logging` — remove only those no longer used elsewhere in the file).

`src/phil/cli/main.py`:
- Add `import contextlib` to the standard imports and `from phil import __version__, diagnostics, platform`.
- In `_chat`: `from phil.chat.session import LOG_FILE, ChatLocked, ChatSession, list_open_chats`, and replace `with chat_logging(session.dir):` with `with diagnostics.redirect(session.dir / LOG_FILE):`.
- Add near `_factory_from_env`:

```python
def _worker_log(start: Path, run_id: str) -> Path | None:
    """The run's worker.log, where the worker's stdout and stderr already go; None outside a repository."""
    try:
        return ProjectPaths(resolve_repo(start).slug).run_dir(run_id) / "logs" / "worker.log"
    except RepoError:
        return None
```

- In `worker(...)`, wrap the existing body after `start = …`:

```python
    start = ctx.obj.get("repo") or Path.cwd()
    log = _worker_log(start, run_id)
    with diagnostics.redirect(log) if log is not None else contextlib.nullcontext():
        try:
            outcome = run_worker(start, run_id, mode, json.loads(decision) if decision else None, factory=_factory_from_env())
        except WorkerError as exc:
            console.print(f"[phil.error]{escape(str(exc))}[/]")
            raise typer.Exit(2) from exc
        if outcome.status == "stopped":
            from phil.workspace import shell

            # A tool thread may have started a command after the stop handler's own kill.
            shell.kill_active_groups()
        console.print(f"{escape(run_id)}: {escape(outcome.status)}")
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/chat tests/cli -n 0 -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/phil/chat/session.py src/phil/cli/main.py tests/chat/test_watcher.py tests/cli/test_worker_diagnostics.py
# message: "Chat and worker: every library's diagnostics go to their own logs"
git commit -F .git/phil-commit-msg.txt
```

---

### Task 4: Strict warnings in tests, and docs

**Files:**
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`), `README.md` (Development), `docs/superpowers/roadmap.md` (Release sequence)
- Modify: whatever source files the strict filter exposes (fixed at their cause)
- Test: `tests/test_diagnostics.py` (one more test)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_diagnostics.py`:

```python
def test_a_warning_raised_in_phil_code_fails_the_tests():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    options = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert "error:::phil" in options.get("filterwarnings", [])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_diagnostics.py -n 0 -q -k fails_the_tests`
Expected: FAIL, `KeyError` or the assertion (no `filterwarnings` yet).

- [ ] **Step 3: Add the filter and run the whole suite**

`pyproject.toml`, in `[tool.pytest.ini_options]` after `addopts`:

```toml
filterwarnings = ["error:::phil"]
```

Run: `uv run pytest -q > .superpowers/sdd/2026-10-21-phil-diagnostics/task4-suite.txt 2>&1; tail -30 .superpowers/sdd/2026-10-21-phil-diagnostics/task4-suite.txt`
Expected: green. If tests now fail with a warning raised from a `phil.*` module, fix each at its cause (an upgrade, or the deprecated call replaced), one at a time, re-running the failing test after each; ledger each as a ruling with the warning's text. Never add an ignore entry for a `phil` warning.

- [ ] **Step 4: Docs**

`README.md`, Development section, after the `uv tool install --editable` paragraph:

```markdown
Phil keeps library warnings, other libraries' log lines and crash tracebacks out of the terminal.
They go to `~/.phil/logs/phil.log` (a chat's go to its own `phil.log`, a run's to its
`worker.log`), and a crash prints one line naming its file in `~/.phil/logs/`. To see them in the
terminal while developing, set `PHIL_DEBUG=1`:

    PHIL_DEBUG=1 uv run phil
```

`docs/superpowers/roadmap.md`, Release sequence: mark skills as moved after launch, record the order, and tick diagnostics:

```markdown
3. ~~Skills~~ — moved to after launch (user, 2026-10-20).
4. Release work, in this order (user, 2026-10-20):
   - ✓ keep internal diagnostics out of users' terminals ([spec](specs/2026-10-20-phil-diagnostics-design.md), [plan](plans/2026-10-21-phil-diagnostics.md));
   - confirm the bring-your-own-provider path (M2a);
   - finalize the mascot;
   - the TestPyPI rehearsal, then publish `phil-agent` by tagging `v0.1.0`.
```

and add as the first "After launch" item: `Skills, a basic version: Phil and its sub-agents use the SKILL.md skills the user already has installed, from folders the user approves.` (renumber the rest). Replace the old items 3–5 text accordingly; keep item 5's status-bar/chat cleanup line under the polish pass without the mascot.

- [ ] **Step 5: Run the whole suite and commit**

Run: `uv run pytest -q > .superpowers/sdd/2026-10-21-phil-diagnostics/task4-final.txt 2>&1; tail -5 .superpowers/sdd/2026-10-21-phil-diagnostics/task4-final.txt`
Expected: all pass.

```bash
git add pyproject.toml README.md docs/superpowers/roadmap.md tests/test_diagnostics.py <any source files fixed>
# message: "Tests fail on Phil's own warnings; docs for PHIL_DEBUG"
git commit -F .git/phil-commit-msg.txt
```
