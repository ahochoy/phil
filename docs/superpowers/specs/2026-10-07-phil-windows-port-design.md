# Phil: Native Windows Port

**Status:** approved in conversation, 2026-10-07.

## 1. Problem

CI (PR #27) runs Phil's offline tests on Ubuntu, macOS and Windows. Ubuntu and macOS pass.

On Windows, Phil can't import `workspace/shell.py`: it uses `signal.SIGKILL` as a default argument. A throwaway probe (PR #28) worked past the import blockers and ran the whole suite:

- **Result:** 51 failed, 1949 passed.
- **The worst bug:** `os.kill(pid, 0)`, Phil's worker liveness check, sends **Ctrl-C to the whole console** on Windows, because signal 0 there is `CTRL_C_EVENT`.

The user wants Phil to run fully on native Windows, not only through WSL. If the port proved too costly, WSL would be the stopgap.

## 2. Decisions (user, 2026-10-07)

| Topic | Decision |
|---|---|
| Windows model | Native Windows; agent commands run through Git Bash. |
| Git Bash | **Required on native Windows.** Phil already needs git for branches, worktrees and PRs, and Git for Windows ships Git Bash. WSL users run Phil as Linux and need nothing extra. |
| Process control | Use `psutil` (7.2.2; wheels for CPython 3.14 on Windows, macOS and Linux). |

## 3. The failure inventory (PR #28, Windows)

| # | Cause | Failures |
|---|---|---|
| 1 | File locking: no `fcntl` (learnings notes, so merge cleanup fails) | ~16 |
| 2 | Process lifecycle: `killpg`/`SIGKILL`, `WNOHANG` reaping, `phil stop` via `SIGTERM`, liveness via `os.kill(pid, 0)`, `SIGALRM` | ~15 |
| 3 | Command parsing: quoted Windows paths fail the allowlist match | ~7 |
| 4 | Missing-command detection: a Windows "file not found" error instead of exit 127 | ~4 |
| 5 | Text encoding: the cp1252 default garbles or crashes on non-ASCII | 3 |
| 6 | File modes: POSIX modes from `fchmod` don't apply | 2 |
| 7 | Line endings: `\r\n` changes byte counts | 1 |

## 4. Design

### 4.1 One platform module

Add `src/phil/platform.py`. It holds every OS difference, so the rest of Phil asks it rather than branching on `os.name`:

- `IS_WINDOWS`
- `pid_alive(pid) -> bool`
- `kill_tree(pid)`
- `detach_flags() -> dict` (the `Popen` keyword arguments)
- `request_stop(pid)`
- `lock_file(handle)` / `unlock_file(handle)`
- `find_bash() -> Path | None`

The POSIX branches keep today's behaviour exactly.

### 4.2 Running agent commands

- **Parsing and checks are unchanged on every OS:**
  - the command is parsed with `shlex.split` (POSIX rules);
  - it's checked against the allowlist, the read-only rules and approvals;
  - with no shell syntax, as today.
- **Before matching, Windows paths are normalised to forward slashes, in both the command and the allowlist patterns.** That makes `'D:\…\python.exe' hello.py` match its pattern.
- **On POSIX:** the parsed argv runs directly, as today.
- **On Windows:** the parsed, already-checked argv runs as `[bash, "-c", shlex.join(argv)]`. The bash binary comes from `find_bash()`.
  - Bash resolves `npm`, `.cmd` wrappers and Git Bash tools.
  - A missing command exits 127, so `cmd_not_found` keeps working.
  - No user text reaches bash unquoted: `shlex.join` quotes every argument.
- **Timeouts and tree kills:** on timeout or cancellation, Phil kills the whole process tree with `psutil` (children recursively, then the root), instead of `os.killpg`. The registry of active process groups becomes a registry of active root pids. Nothing uses `SIGALRM`.
- **Setup commands** (`[project] setup_cmd`) run through the same path.

### 4.3 Finding Git Bash

`find_bash()` checks these in order and returns the first that exists:

1. `[shell] bash`, a new config key (default `None`).
2. Derived from `git` on `PATH`: the directory of `shutil.which("git")` gives `<Git>\cmd\git.exe` or `<Git>\bin\git.exe`, and bash is `<Git>\bin\bash.exe`.
3. `%ProgramFiles%\Git\bin\bash.exe`, then `%LocalAppData%\Programs\Git\bin\bash.exe`.

It **never** returns `%SystemRoot%\System32\bash.exe`, the WSL launcher, which would run commands in the wrong system. On POSIX it isn't used.

**When none is found on Windows:**
- `phil`, `phil run`, `phil setup` and `phil models check` stop before doing anything else.
- The message is: `Phil on Windows needs Git for Windows (it includes Git Bash): https://git-scm.com/download/win — or set [shell] bash to your bash.exe.`
- `phil --help`, `phil config` and `phil keys` still work.

### 4.4 Workers

- **Liveness:** `pid_alive` uses `psutil.pid_exists`, plus a not-a-zombie check. It replaces `os.kill(pid, 0)` in `run/launch.py` and `chat/session.py` (the chat lock) on every OS, which removes the Ctrl-C bug.
- **Spawning:** the worker starts with `detach_flags()`:
  - `start_new_session=True` on POSIX;
  - `creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows.
- **Reaping:** the `os.waitpid(…, WNOHANG)` zombie reaping runs on POSIX only. Windows has no zombies.
- **Stopping:** `phil stop` calls `request_stop(pid)`. On POSIX that sends `SIGTERM`, as today. On Windows it sends `CTRL_BREAK_EVENT` to the worker's process group. The worker installs the same handler for `SIGBREAK` that it installs for `SIGTERM`: stop cleanly, and record "stopped by user". If the worker hasn't stopped within the existing timeout, `phil stop` kills its tree.

### 4.5 Mechanical fixes

- **File locks:** `lock_file`/`unlock_file` use `fcntl.flock` on POSIX and `msvcrt.locking` (on the first byte, blocking) on Windows. `publish/learnings.py` uses them.
- **Encoding:** every text read and write in `src/` passes `encoding="utf-8"`. The console is reconfigured to UTF-8 at startup on Windows (`sys.stdout`/`sys.stderr.reconfigure(encoding="utf-8")`). Subprocess output is decoded as UTF-8 with `errors="replace"`.
- **Line endings:** files Phil writes (event logs, JSON, TOML, markdown) use `newline="\n"`.
- **File modes:** the POSIX mode set on the global config is skipped on Windows, where the config holds no keys and inherits the user profile's ACL. Tests that assert mode bits run on POSIX only.

### 4.6 Tests

- **The inventoried failures pass on Windows.** Tests whose subject is POSIX-only (mode bits, `SIGTERM` itself, zombies) get `skipif(IS_WINDOWS)` with a reason. Each one has a Windows counterpart where the behaviour exists there (for example `CTRL_BREAK` stopping a worker).
- **New tests on every OS:**
  - `find_bash` order, and refusing the WSL launcher;
  - Windows argv wrapping (a pure function, tested on all OSes);
  - path normalisation in the allowlist;
  - `pid_alive` never signalling;
  - the lock helper.

### 4.7 CI and docs

- When Windows passes, remove `continue-on-error` from `.github/workflows/tests.yml`. The user then adds `tests (windows-latest)` to the required checks. GitHub's Windows runners include Git Bash.
- README: a Windows section covering installing Git for Windows, that agent commands run through Git Bash, `[shell] bash`, and that WSL works as Linux.

## 5. Out of scope

- PowerShell as the agents' shell, and Windows-specific agent prompts.
- Packaging and publishing (the next work stream).
- Windows ARM-specific testing beyond what CI covers.

## 6. Done means

- CI's Windows job passes with `continue-on-error` removed.
- Ubuntu and macOS stay green.
- A live smoke run on a Windows machine, by the user, plans and runs a quick change end to end.
