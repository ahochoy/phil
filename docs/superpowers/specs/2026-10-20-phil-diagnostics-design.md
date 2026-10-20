# Diagnostics kept out of users' terminals — design

Status: approved in conversation, 2026-10-20. Release work item 4a in `docs/superpowers/roadmap.md`
("keep internal diagnostics out of users' terminals"); user direction recorded in
`plans/2026-09-24-phil-04a-followups.md`, "Before a public release".

## 1. Goal

People who install Phil never see a raw library warning, another library's log line, or a Python
traceback. Everything hidden from them is kept in a log file, so a bug report can include it. Phil's
developer sees all of it when they ask to, and the test suite fails on warnings Phil itself causes,
so causes get fixed rather than silenced.

Deliberate user-facing output is out of scope and stays as it is: the `[phil.error]` lines for a
bad config, a missing key or a provider's rejection, the chat's callouts, and every command's
normal output.

## 2. What happens today

- In a chat, `chat_logging` (`src/phil/chat/session.py`) sends the `phil` loggers to the chat's
  `phil.log` and nowhere else.
- A background worker's stdout and stderr go to `runs/<id>/logs/worker.log`
  (`src/phil/run/launch.py`).
- `phil.cli.main`'s logger has a `NullHandler`.

The gaps, each of which can put raw text in a user's terminal:

1. **Python warnings.** A library's `UserWarning`, `FutureWarning` or beta warning prints to stderr.
   This is the 2026-09-28 incident (pydantic's Python 3.14 compatibility warning). Nothing routes
   warnings anywhere.
2. **Other libraries' loggers.** Only `phil` has a handler. A `WARNING` from httpx, langchain or
   openai reaches Python's last-resort handler, which writes it to stderr — inside a chat too,
   above the prompt.
3. **Crashes.** An uncaught exception gets Typer's pretty traceback, which shows local variables by
   default: a screen of text that can include a key or a prompt.
4. **No developer view.** There is no switch that brings these diagnostics back.

## 3. Decisions

| Question | Decision |
|---|---|
| How the developer turns on the developer view | The environment variable `PHIL_DEBUG=1`. It covers every command, the chat, and spawned workers, which inherit the environment. No flag. |
| What a user sees when Phil crashes | One line and the path of a crash file: `Phil hit an unexpected error (KeyError). Details were saved to <path> — please include it if you report this.` Exit code 1. |
| Where hidden diagnostics are kept | Where Phil already logs: a chat's own `phil.log`, a worker's `worker.log`, and for every other command a size-capped `<phil home>/logs/phil.log`. Crash files go in `<phil home>/logs/`. |
| How strict the tests are | A warning raised from Phil's own code fails the test. Library warnings stay in pytest's summary for the developer to act on, usually by upgrading. |

Rejected approaches: silencing warnings and raising library log levels (hides problems and keeps no
record — the user's direction is to fix causes, not suppress them), and capturing stderr inside the
chat's UI (covers only the chat, and fights the prompt's redraws).

## 4. Design

### 4.1 `src/phil/diagnostics.py` (new)

- `debug_enabled() -> bool` — true when `PHIL_DEBUG` is `1` (also `true`/`yes`, any case).
- `configure(log_path: Path) -> None` — run once per process, before the heavy imports; safe to call
  again (a second call replaces what the first installed rather than adding to it).
  - `logging.captureWarnings(True)`, so `warnings.warn` becomes a record on the `py.warnings` logger.
    Warning filters are left as they are: they still decide whether a warning fires; the handlers
    decide where it goes.
  - The root logger gets a `RotatingFileHandler` on `log_path` (1 MB, 3 backups, UTF-8, created on
    first write), at `INFO`. With a handler on the root, no record ever reaches the last-resort
    handler.
  - With `PHIL_DEBUG=1`, also a stderr handler: `DEBUG` for `phil.*`, `WARNING` for everything
    else, so httpx's request lines don't flood the screen.
  - If the log directory can't be created or written (read-only home, full disk), it installs a
    `NullHandler` instead. Nothing reaches the terminal either way.
- `redirect(log_path: Path)` — a context manager that points the file handler at another file while
  it is active, and back afterwards. It replaces `chat_logging`, and unlike it covers the root
  logger, not only `phil`.
- `crash(exc: BaseException, command: str | None) -> Path | None` — writes
  `<phil home>/logs/crash-<UTC timestamp>-<pid>.log` holding the traceback (no local variables),
  Phil's version, Python's version, the OS, and the command name. It never writes argv values,
  which can hold a key (`phil keys set`). It keeps the newest 20 crash files and deletes older
  ones. Returns the path, or `None` if the file couldn't be written.

### 4.2 Entry point

- New `src/phil/cli/entry.py` with `main()` and `run(app)`. `entry.py` imports only the standard
  library and `phil.diagnostics` at module level. `main()` calls
  `configure(phil_home() / "logs" / "phil.log")` first, then imports `phil.cli.main`, so warnings
  raised while langchain or pydantic import are captured too, then calls `run(app)`. Tests call
  `run` with an app of their own.
- `run(app)`:
  1. `configure(...)` again (a no-op if already done), so `run` also works on its own.
  2. Calls the app. `typer.Exit`, Click's exit and abort, and `SystemExit` pass through unchanged.
     `KeyboardInterrupt` exits quietly with 130, as today.
  3. Any other exception: with `PHIL_DEBUG=1` it is re-raised, giving Python's plain traceback.
     Otherwise `crash(...)` writes the file and the one line is printed to stderr — without the
     path if the file couldn't be written — and the process exits 1.
- `pyproject.toml`: `[project.scripts] phil = "phil.cli.entry:main"`.
- `src/phil/__main__.py` calls `phil.cli.entry.main()`, so the worker (`python -P -m phil … _worker`)
  goes through it too.
- The Typer app is created with `pretty_exceptions_enable=False`. Even in the developer view, local
  variables are never printed.

### 4.3 Chat and worker

- **Chat:** `with diagnostics.redirect(session.dir / "phil.log")` replaces `with chat_logging(...)`.
  `chat_logging` and its tests are removed or rewritten against `redirect`.
- **Worker:** the `_worker` command runs inside `redirect(<run dir>/logs/worker.log)`. Its stderr
  already goes to that file, so even a raw write from a C extension ends up there. A worker crash
  writes a crash file, prints its one line into `worker.log`, and the run fails through the existing
  path, which the chat already reports.

## 5. Tests

- **Subprocess tests** (`tests/test_diagnostics_cli.py`) run a small script with `python -I`, a
  temporary `PHIL_HOME`, and a Typer app of the test's own passed to `phil.cli.entry.run`. Its
  commands raise a `UserWarning`, log a `WARNING` from a non-`phil` logger, and raise an exception.
  - Without `PHIL_DEBUG`: stderr is empty for the warning and the log line, and both are in
    `<home>/logs/phil.log`. The crash prints exactly the one line, exits 1, and writes a crash file
    with the traceback but without the local variable's value the test planted.
  - With `PHIL_DEBUG=1`: stderr shows the warning and the log line, and the crash shows a traceback
    without locals.
  - `KeyboardInterrupt` exits 130 with no crash file.
- **Unit tests** (`tests/test_diagnostics.py`): `redirect` switches files and switches back;
  `configure` twice leaves one file handler; an unwritable log directory leaves stderr clean;
  crash files are capped at 20; `debug_enabled` values.
- **Entry tests:** `[project.scripts]` points at `phil.cli.entry:main`, and `phil --version` still
  prints the version.
- `pyproject.toml` `[tool.pytest.ini_options]`: `filterwarnings = ["error:::phil"]`, so a warning
  attributed to a `phil` module fails the test. Any warning this exposes today is fixed at its cause
  in this work.

## 6. Out of scope

- A `--debug` flag, auto-detecting an editable install, or an issue-tracker link in the crash line.
- Rewording existing `[phil.error]` lines.
- Uploading crash reports anywhere.

## 7. Docs

- `CONTRIBUTING.md` (after PR #50 merges; otherwise the README's development section): `PHIL_DEBUG=1`
  and where the logs and crash files are.
- `docs/superpowers/roadmap.md`: mark the diagnostics item done, record the 2026-10-20 order
  (diagnostics → provider checks → mascot → rehearsal and tag), and move skills to after launch.
