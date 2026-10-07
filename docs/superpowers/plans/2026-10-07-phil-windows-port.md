# Native Windows Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phil runs fully on native Windows, with agent commands run through Git Bash, and the Windows CI job passes as a required check.

**Architecture:** `src/phil/platform.py` holds every OS difference, using `psutil` for processes. The command runner, workers and file locks call it, and their POSIX behaviour stays exactly as today. On Windows, an already-parsed, already-checked argv runs as `bash -c <shlex.join(argv)>`. Mechanical fixes cover UTF-8, line endings and file modes. A final task iterates on the Windows CI job until it's green.

**Tech Stack:** Python 3.14, psutil 7.2.2, pytest, GitHub Actions (Windows runner).

**Spec:** `docs/superpowers/specs/2026-10-07-phil-windows-port-design.md`. Its §3 is the failure inventory from PR #28.

## Global Constraints

- **Editing and committing**
  - Edit files only with the Edit or Write tools. Never edit through python, perl, sed, heredocs or printf in Bash.
  - Commits: write the message to a file with Write, then run `git commit -F <file>`.
  - Every message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Keep the words "keychain" and "credentials" out of Bash command lines.
- **Hooks, secrets and live runs**
  - Never work around a hook or guard. If one blocks you, stop and report BLOCKED.
  - Never read or print `.env` files or key values.
  - Never run `-m live` or `-m bench`.
- **Local testing**
  - You're on macOS, so Windows code paths are tested locally only through pure functions and `monkeypatch` of `phil.platform.IS_WINDOWS` and its helpers.
  - The real Windows run is CI (Task 5).
  - Iterate with `uv run pytest <paths> -q -n 0`. Run the full `uv run pytest -q` once at the end of each task.
- **POSIX behaviour is unchanged.** Every existing macOS/Linux test keeps passing, without edits unless a task says otherwise.
- **The missing-bash message, verbatim:** `Phil on Windows needs Git for Windows (it includes Git Bash): https://git-scm.com/download/win — or set [shell] bash to your bash.exe.`
- **`find_bash()` order:**
  1. `[shell] bash`;
  2. derived from `shutil.which("git")`: its parent is `cmd` or `bin` under `<Git>`, giving `<Git>\bin\bash.exe`;
  3. `%ProgramFiles%\Git\bin\bash.exe`;
  4. `%LocalAppData%\Programs\Git\bin\bash.exe`.

  It never returns a path under `%SystemRoot%\System32` (the WSL launcher).
- **Dependency:** `psutil>=7.2.2`, added to `pyproject.toml` and locked with `uv lock`.

## Review Focus

1. **A command with a quoted Windows path** (`'C:\x\python.exe' a.py`) is checked against a pattern written with backslashes or with forward slashes, and both match. Tested in Task 2.
2. **An allowlisted argument containing spaces, quotes or `$`** reaches bash as a single literal argument: `shlex.join` keeps it from being expanded. Tested in Task 2.
3. **Git installed with `git.exe` under `<Git>\mingw64\bin`** (some installs put that on PATH). `find_bash` still finds `<Git>\bin\bash.exe`, by walking up to the directory containing `bin\bash.exe`. Tested in Task 1.
4. **`phil stop` on a worker that ignores the stop request:** it falls back to killing the tree after the timeout, and reports what it did. Tested in Task 3.
5. **`pid_alive` for a pid that belongs to another user:** it returns True and never raises. Tested in Task 1.

---

### Task 1: `phil.platform`, psutil, and finding bash

**Files:**
- Create: `src/phil/platform.py`, `tests/test_platform.py`
- Modify: `pyproject.toml` and `uv.lock` (add `psutil>=7.2.2`); `src/phil/config.py` (`ShellConfig.bash: str | None = None`)

**Interfaces:**
- Produces, in `phil.platform`:

| Name | Type or signature |
|---|---|
| `IS_WINDOWS` | `bool` |
| `pid_alive` | `(pid: int) -> bool` |
| `kill_tree` | `(pid: int) -> None` |
| `detach_kwargs` | `() -> dict` |
| `request_stop` | `(pid: int) -> None` |
| `STOP_SIGNALS` | `tuple[int, ...]` |
| `lock_file` / `unlock_file` | `(handle) -> None` |
| `find_bash` | `(configured: str \| None = None, *, which=shutil.which, environ=os.environ, exists=os.path.exists) -> Path \| None` |
| `windows_argv` | `(argv: list[str], bash: Path) -> list[str]` |
| `MISSING_BASH` | `str` (the verbatim message) |

- [ ] **Step 1: Write the failing tests**

Create `tests/test_platform.py`:

```python
import os
import subprocess
import sys
from pathlib import PureWindowsPath

import pytest

from phil import platform


def test_pid_alive_for_this_process_and_not_for_a_finished_one():
    assert platform.pid_alive(os.getpid())
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not platform.pid_alive(proc.pid)


def test_pid_alive_never_signals(monkeypatch):
    # On Windows, os.kill(pid, 0) sends CTRL_C_EVENT to the console: it must never be used.
    monkeypatch.setattr(os, "kill", lambda *a: (_ for _ in ()).throw(AssertionError("os.kill called")))
    assert platform.pid_alive(os.getpid())


def test_pid_alive_is_true_for_another_users_process(monkeypatch):
    import psutil

    class Denied:
        def __init__(self, pid):
            raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", Denied)
    assert platform.pid_alive(4242)


def test_kill_tree_kills_children_too():
    import time

    code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
    proc = subprocess.Popen([sys.executable, "-c", code])
    time.sleep(1.0)
    import psutil

    children = psutil.Process(proc.pid).children(recursive=True)
    platform.kill_tree(proc.pid)
    proc.wait(timeout=10)
    gone, alive = psutil.wait_procs(children, timeout=10)
    assert not alive


def git_layout(root):
    """which/exists fakes for a Git for Windows install at `root`."""
    git = str(PureWindowsPath(root, "cmd", "git.exe"))
    bash = str(PureWindowsPath(root, "bin", "bash.exe"))
    return (lambda name: git if name == "git" else None), (lambda path: str(path) == bash), bash


def test_find_bash_prefers_the_configured_path():
    which, exists, _ = git_layout(r"C:\Program Files\Git")
    found = platform.find_bash(r"D:\tools\bash.exe", which=which, environ={}, exists=lambda p: True)
    assert str(found) == r"D:\tools\bash.exe"


def test_find_bash_derives_from_git_on_path():
    which, exists, bash = git_layout(r"C:\Program Files\Git")
    assert str(platform.find_bash(None, which=which, environ={}, exists=exists)) == bash


def test_find_bash_walks_up_from_a_mingw64_git():
    bash = str(PureWindowsPath(r"C:\Git", "bin", "bash.exe"))
    which = lambda name: r"C:\Git\mingw64\bin\git.exe" if name == "git" else None  # noqa: E731
    assert str(platform.find_bash(None, which=which, environ={}, exists=lambda p: str(p) == bash)) == bash


def test_find_bash_falls_back_to_program_files():
    bash = str(PureWindowsPath(r"C:\Program Files", "Git", "bin", "bash.exe"))
    found = platform.find_bash(None, which=lambda n: None, environ={"ProgramFiles": r"C:\Program Files"},
                               exists=lambda p: str(p) == bash)
    assert str(found) == bash


def test_find_bash_never_returns_the_wsl_launcher():
    wsl = r"C:\Windows\System32\bash.exe"
    found = platform.find_bash(wsl, which=lambda n: None, environ={"SystemRoot": r"C:\Windows"}, exists=lambda p: True)
    assert found is None


def test_windows_argv_quotes_every_argument_for_bash():
    argv = ["npm", "test", "--", "a b", "$HOME", "it's"]
    wrapped = platform.windows_argv(argv, PureWindowsPath(r"C:\Git\bin\bash.exe"))
    assert wrapped[:2] == [r"C:\Git\bin\bash.exe", "-c"]
    assert wrapped[2] == "npm test -- 'a b' '$HOME' 'it'\"'\"'s'"


def test_lock_file_round_trip(tmp_path):
    path = tmp_path / "lock"
    with path.open("a+", encoding="utf-8") as handle:
        platform.lock_file(handle)
        platform.unlock_file(handle)


def test_detach_kwargs_on_posix():
    if platform.IS_WINDOWS:
        pytest.skip("POSIX shape")
    assert platform.detach_kwargs() == {"start_new_session": True}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_platform.py -q -n 0`
Expected: FAIL (no `phil.platform`).

- [ ] **Step 3: Implement**

Add `"psutil>=7.2.2",` to `[project] dependencies` in `pyproject.toml`, then run `uv lock` and `uv sync`.

Add to `ShellConfig` in `src/phil/config.py`:

```python
    bash: str | None = None  # Windows only: Git Bash's bash.exe, when Phil can't find it
```

Create `src/phil/platform.py`:

```python
"""Every OS difference Phil depends on, in one place (spec 2026-10-07). POSIX keeps the
behaviour Phil always had; Windows runs agent commands through Git Bash and controls
processes with psutil. Never use os.kill(pid, 0) to probe a process: on Windows signal 0 is
CTRL_C_EVENT, which interrupts the whole console."""

import os
import shlex
import shutil
import signal
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path, PureWindowsPath

import psutil

IS_WINDOWS = os.name == "nt"
MISSING_BASH = (
    "Phil on Windows needs Git for Windows (it includes Git Bash): https://git-scm.com/download/win"
    " — or set [shell] bash to your bash.exe."
)
# What stops a worker cleanly: SIGTERM on POSIX; on Windows `phil stop` sends CTRL_BREAK_EVENT,
# which Python delivers as SIGBREAK.
STOP_SIGNALS: tuple[int, ...] = (signal.SIGBREAK,) if IS_WINDOWS else (signal.SIGTERM,)


def pid_alive(pid: int) -> bool:
    """True if `pid` is a running process (a zombie counts as gone). Never signals it."""
    if pid <= 0 or not psutil.pid_exists(pid):
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True  # alive, owned by someone else


def kill_tree(pid: int) -> None:
    """Kill `pid` and every descendant. Missing processes are ignored."""
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for proc in [*children, root]:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs([*children, root], timeout=5)


def detach_kwargs() -> dict:
    """Popen keyword arguments that detach a child from Phil's console and process group."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def request_stop(pid: int) -> None:
    """Ask a worker to stop cleanly (it handles STOP_SIGNALS). Raises ProcessLookupError if gone."""
    if IS_WINDOWS:
        os.kill(pid, signal.CTRL_BREAK_EVENT)
    else:
        os.kill(pid, signal.SIGTERM)


def lock_file(handle) -> None:
    if IS_WINDOWS:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX)


def unlock_file(handle) -> None:
    if IS_WINDOWS:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_UN)


def _is_wsl_launcher(path: PureWindowsPath, environ: Mapping[str, str]) -> bool:
    system32 = PureWindowsPath(environ.get("SystemRoot", r"C:\Windows"), "System32")
    return str(path).lower().startswith(str(system32).lower())


def find_bash(
    configured: str | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] = os.environ,
    exists: Callable[[object], bool] = os.path.exists,
) -> Path | None:
    """Git Bash's bash.exe (spec §4.3), or None. Never the System32 WSL launcher."""
    candidates: list[PureWindowsPath] = []
    if configured:
        candidates.append(PureWindowsPath(configured))
    git = which("git")
    if git:
        for parent in PureWindowsPath(git).parents:
            candidates.append(parent / "bin" / "bash.exe")
    if environ.get("ProgramFiles"):
        candidates.append(PureWindowsPath(environ["ProgramFiles"], "Git", "bin", "bash.exe"))
    if environ.get("LocalAppData"):
        candidates.append(PureWindowsPath(environ["LocalAppData"], "Programs", "Git", "bin", "bash.exe"))
    for candidate in candidates:
        if not _is_wsl_launcher(candidate, environ) and exists(candidate):
            return Path(str(candidate)) if IS_WINDOWS else Path(str(candidate).replace("\\", "/"))
    return None


def windows_argv(argv: list[str], bash: os.PathLike | str) -> list[str]:
    """The argv Windows runs: bash -c with every (already checked) argument quoted."""
    return [str(bash), "-c", shlex.join(argv)]
```

The `find_bash` tests compare `str(found)` with Windows-style strings. If `Path(...)` on macOS changes the separators, adjust the return so that the tests' `str()` comparisons hold on every OS: return `PureWindowsPath` when the input was Windows-style, and keep the return annotation honest. Keep every assertion.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_platform.py tests/test_config.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Add phil.platform: OS differences in one place, with psutil and Git Bash discovery`, plus the trailer.

---

### Task 2: Agent commands on Windows

**Files:**
- Modify: `src/phil/workspace/shell.py`:
  - `run_command`, around line 556;
  - `kill_active_groups`, around line 544;
  - `_ACTIVE_GROUPS` (rename it `_ACTIVE_ROOTS`);
  - the allowlist matching in `_classify`, around line 465.
- Modify: `src/phil/cli/main.py`, adding the missing-bash refusal to the commands named in spec §4.3.
- Test: `tests/workspace/test_shell.py`, `tests/agents/test_tools.py`, `tests/cli/` (a new missing-bash test).

**Interfaces:**
- Consumes: `phil.platform` (Task 1).
- Produces:
  - `shell.kill_active_groups()`, same name and no signal argument, which kills every active command tree;
  - `shell.normalise_path_text(text: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/workspace/test_shell.py`:
- **Normalisation:** `normalise_path_text(r"'C:\x\python.exe' a.py") == "'C:/x/python.exe' a.py"`.
- **A Windows-path command matches either pattern style.** A command with a quoted backslash path is allowed by an allowlist pattern written with backslashes, and by one written with forward slashes. Build the policy the way the existing allowlist tests do, read them first, and monkeypatch nothing OS-specific: normalisation runs on every OS.
- **Windows wrapping.**
  - Monkeypatch `phil.platform.IS_WINDOWS` to True, and `phil.workspace.shell.find_bash` (or the name the shell module uses) to return a fake bash.
  - Capture `subprocess.Popen`'s argv with a fake. The argv must be `[bash, "-c", "npm test -- 'a b'"]` for the command `npm test -- 'a b'`.
  - With `find_bash` returning None, `run_command` returns exit code 127 with `MISSING_BASH` in stderr.
- **Timeouts kill the tree.** A command that starts a sleeping child and times out leaves no child alive. Adapt the existing timeout and child-pid tests from `os.killpg` to `phil.platform.kill_tree`. The existing `SIGALRM`-based test stays POSIX-only, with `@pytest.mark.skipif(platform.IS_WINDOWS, reason="SIGALRM is POSIX-only")`.

Add to `tests/cli/` a test that the missing-bash refusal blocks `phil run`. With `IS_WINDOWS` patched True and `find_bash` returning None, `phil run <plan>` exits non-zero with `MISSING_BASH`, while `phil config` still works. Follow the existing CLI test patterns (typer's `CliRunner`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/workspace/test_shell.py tests/cli -q -n 0`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

In `shell.py`:
- **Path normalisation.** Add `normalise_path_text(text)`: replace every backslash with a forward slash. Phil's allowlist is POSIX-style, and a backslash is never meaningful in an allowed command on any OS. Apply it to the command text before `shlex.split` in `_classify`, and to each pattern before `shlex.split(literal_pattern(pattern))`, for the approved, extra and config allow lists.
  - Make sure this doesn't change POSIX behaviour for commands that contain no backslash.
  - A command containing a backslash on POSIX is now matched with forward slashes. That's acceptable, because backslashes in agent commands are not a supported form. Note it in the docstring.
- **`run_command`.**
  - Build `args = shlex.split(normalise_path_text(command))`.
  - When `platform.IS_WINDOWS`, wrap it as `platform.windows_argv(args, bash)`, where `bash = platform.find_bash(load-time configured value)`. Pass the configured `[shell] bash` in through a module-level setter or a keyword argument. Read how `run_command`'s callers have the config, and choose the least invasive route.
  - If `find_bash` returns None, return `ShellResult(command, 127, "", platform.MISSING_BASH, False, elapsed())`.
  - Replace `start_new_session=True` with `**platform.detach_kwargs()`.
  - Replace both `os.killpg(proc.pid, signal.SIGKILL)` calls with `platform.kill_tree(proc.pid)`.
  - Rename `_ACTIVE_GROUPS` to `_ACTIVE_ROOTS`.
- **`kill_active_groups()`** loses its `sig` parameter. It calls `platform.kill_tree` for each root, returning the list as before.
- **`publish/publisher.py:95`.** Replace `start_new_session=True` with `**platform.detach_kwargs()`.

In `cli/main.py`, add a helper:

```python
def _require_bash(config) -> None:
    from phil import platform

    if platform.IS_WINDOWS and platform.find_bash(config.shell.bash) is None:
        console.print(f"[phil.error]{escape(platform.MISSING_BASH)}[/]", soft_wrap=True)
        raise typer.Exit(1)
```

Call it once the config is loaded in the chat entry point, `phil run`, `phil setup` and `phil models check`. Don't call it in `phil config`, `phil keys` or `--help`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/workspace tests/agents tests/cli -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Run agent commands through Git Bash on Windows; kill command trees with psutil`, plus the trailer.

---

### Task 3: Workers on Windows

**Files:**
- Modify: `src/phil/run/launch.py`:
  - `spawn_worker`, around line 61;
  - `is_worker_alive`, around line 79;
  - `worker_starting`, around line 94.
- Modify: `src/phil/chat/session.py` (`_pid_alive`, around line 40); `src/phil/run/worker.py` (its `SIGTERM` handler, around line 160); `src/phil/cli/main.py` (`stop`, around line 790).
- Test: `tests/run/test_launch.py`, `tests/run/test_worker.py`, `tests/cli/test_stop_command.py`, `tests/chat/test_overview_session.py`.

**Interfaces:**
- Consumes: `platform.pid_alive`, `detach_kwargs`, `request_stop`, `STOP_SIGNALS` and `kill_tree` (Task 1).

- [ ] **Step 1: Write the failing tests**

- **`is_worker_alive` and the chat lock (`session._pid_alive`) never call `os.kill`.** Monkeypatch `os.kill` to raise an AssertionError, as in Task 1's test. A live record (this process's pid, a fresh heartbeat) is alive; a finished subprocess's pid is not.
- **`worker_starting` on Windows** (`IS_WINDOWS` patched True) never calls `os.waitpid`. It uses `pid_alive`.
- **The worker handles every signal in `STOP_SIGNALS`.** Read how `test_real_sigterm_stops_cleanly_and_restores_handler` works.
  - Keep it for POSIX, with `skipif(IS_WINDOWS)`.
  - Add a test that `run_worker` installs its stop handler for each signal in `platform.STOP_SIGNALS` and restores the previous handler afterwards. Assert this through `signal.getsignal` before and after, using a scripted run that stops itself.
- **`phil stop` asks first, then kills.** Monkeypatch `platform.request_stop` to record the call and do nothing, and use a worker that never stops. `phil stop` calls `request_stop`, waits out the timeout (use the command's timeout option set small), then calls `platform.kill_tree`. It reports `stopped by force` (or the existing wording for that path; read the stop command first), not "the worker did not stop in time" as an error.
- **`spawn_worker`** passes `**platform.detach_kwargs()`. Assert it with a fake `Popen` that captures the kwargs.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/run tests/cli/test_stop_command.py tests/chat/test_overview_session.py -q -n 0`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

- **`launch.spawn_worker`:** `**platform.detach_kwargs()` instead of `start_new_session=True`.
- **`launch.is_worker_alive`:** replace the `os.kill(record.pid, 0)` block with `if not platform.pid_alive(record.pid): return False`.
- **`launch.worker_starting`:**
  - keep the `os.waitpid(pid, os.WNOHANG)` reaping on POSIX only (`if not platform.IS_WINDOWS:`);
  - replace the trailing `os.kill(pid, 0)` block with `return platform.pid_alive(pid)`.
- **`session._pid_alive`:** `return platform.pid_alive(pid)`.
- **`worker.run_worker`:** install `_raise_stop` for every signal in `platform.STOP_SIGNALS`, and restore each previous handler in the `finally`. Keep the main-thread check.
- **`cli/main.py` `stop`:**
  - replace `os.kill(record.pid, signal.SIGTERM)` with `platform.request_stop(record.pid)`;
  - if the worker hasn't stopped by the deadline, call `platform.kill_tree(record.pid)`;
  - mark the run stopped the way the existing path does after a kill, and tell the user it was forced.

  Read the existing stop flow and keep its messages for the clean path.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command, plus `uv run pytest tests/chat tests/publish -q -n 0`.
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Workers on Windows: psutil liveness (no os.kill(pid, 0)), detach flags, CTRL_BREAK stop`, plus the trailer.

---

### Task 4: Locks, UTF-8, line endings and file modes

**Files:**
- Modify:
  - `src/phil/publish/learnings.py` (the lock);
  - `src/phil/setup/write.py` (`fchmod`, around line 71);
  - every text `open`, `read_text` and `write_text` in `src/` that lacks `encoding=` (about 53 sites; find them with `grep -rn "open(\|read_text(\|write_text(" src | grep -v "encoding="`);
  - `subprocess.run` and `Popen` calls with `text=True` but no `encoding`;
  - the CLI entry point (UTF-8 console on Windows).
- Test: `tests/publish/test_learnings.py`, `tests/setup/test_write.py`, `tests/store/test_events.py`, a new `tests/test_encoding.py`.

- [ ] **Step 1: Write the failing tests**

- **`tests/test_encoding.py`: no locale-default text I/O in `src/`.** Walk `src/phil/**/*.py` with `ast`. For each call to `open`, `Path.open`, `read_text` and `write_text` with a text mode (no `"b"` in the mode argument), assert that an `encoding` keyword is present. For `subprocess.run`, `check_output` and `Popen` with `text=True` or `universal_newlines=True`, assert `encoding` is present. Report every offending `file:line`.
- **Events and JSON writes use `\n`.** Writing an event, then reading the file in binary, contains no `\r\n`. Read `tests/store/test_events.py`'s byte-count test and keep it, so it now passes on every OS.
- **Learnings** use `platform.lock_file`. Monkeypatch it to record calls; appending a note locks and unlocks once.
- **`write.py`.** The mode assertion tests get `skipif(platform.IS_WINDOWS, reason="POSIX file modes")`. Add a test that with `IS_WINDOWS` patched True, writing the config doesn't call `os.fchmod`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_encoding.py tests/publish/test_learnings.py tests/setup/test_write.py tests/store -q -n 0`
Expected: the encoding audit lists the offending sites and FAILS.

- [ ] **Step 3: Implement**

- **Learnings:** replace the `fcntl` import and `flock` calls with `platform.lock_file(handle)` and `platform.unlock_file(handle)`.
- **`write.py`:** skip `os.fchmod` when `platform.IS_WINDOWS`.
- **Every site the audit lists:**
  - add `encoding="utf-8"`;
  - for writes Phil makes (event logs, JSON, TOML, markdown, the transcript), also `newline="\n"`;
  - for subprocess text output, `encoding="utf-8", errors="replace"`.
- **The CLI entry:** at the top of the typer callback, or `main()`, when `platform.IS_WINDOWS`, run `sys.stdout.reconfigure(encoding="utf-8")` and `sys.stderr.reconfigure(encoding="utf-8")`, guarded with `hasattr(..., "reconfigure")`.
- **Tests that read Phil's files** with `read_text()` and no encoding: add `encoding="utf-8"` where the inventory showed garbling, namely `test_chat_command` (the `·` in the banner), `test_pr_body` (`café`) and `test_show_view`. The audit covers `src/` only, so fix those three tests directly.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command, plus `uv run pytest tests/cli tests/ui tests/publish -q -n 0`.
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Cross-platform file locks, UTF-8 text I/O, LF line endings, POSIX-only file modes`, plus the trailer.

---

### Task 5: Make the Windows CI job pass, then make it required

**Files:**
- Modify: `.github/workflows/tests.yml` (remove `continue-on-error`), `README.md` (a Windows section), and whatever code or tests the Windows CI run shows still failing.

This task runs on CI. It's iterative, because Windows behaviour can only be observed there.

- [ ] **Step 1: Push and open the PR**

The controller pushes the branch and opens a PR against `main`. The workflow runs on Ubuntu, macOS and Windows.

- [ ] **Step 2: Read the Windows result**

```
gh pr checks <PR>
gh run view <run> --job <windows job id> --log
```

List every FAILED and ERROR line, grouped by cause.

- [ ] **Step 3: Fix, test-driven, and push**

For each cause:
- write or adjust a test that pins the behaviour (portable where possible, or with a `skipif` and its Windows counterpart);
- fix it;
- run the local suite;
- commit and push.

Repeat Steps 2–3. **Cap: 5 CI rounds.** If Windows still fails after 5, stop and report the remaining failures with their causes. The controller and the user then decide between continuing and the WSL stopgap (spec §1).

- [ ] **Step 4: Make Windows blocking, and document it**

When the Windows job passes:
- In `.github/workflows/tests.yml`, remove the `continue-on-error` line and its comment.
- In `README.md`, under Getting started, add a "Windows" subsection:
  - Phil runs natively on Windows and needs Git for Windows, which includes Git Bash; give the download link;
  - agent commands run through Git Bash, so allowlists and commands are the same on every OS;
  - set `[shell] bash` if Phil can't find `bash.exe`;
  - Phil also runs inside WSL, as Linux.
- Push. All three jobs must pass.

- [ ] **Step 5: Commit**

Commit message: `Windows CI passes: make it a required job; document Phil on Windows`, plus the trailer.

## After the plan (the user)

- In GitHub Settings → Branches, add `tests (windows-latest)` to the required checks for `main`.
- On a Windows machine with Git for Windows: `uv tool install --editable .`, then `phil setup`, then a quick-path request end to end (spec §6).
