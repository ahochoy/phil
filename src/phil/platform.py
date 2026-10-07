"""Every OS difference Phil depends on, in one place (spec 2026-10-07). POSIX keeps the
behaviour Phil always had; Windows runs agent commands through Git Bash and controls
processes with psutil. Never use os.kill(pid, 0) to probe a process: on Windows signal 0 is
CTRL_C_EVENT, which interrupts the whole console.

A Windows worker is started with CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW rather than
DETACHED_PROCESS: a DETACHED_PROCESS worker has no console at all, so every console child it
spawns (git, python) pops up its own visible console window. CREATE_NO_WINDOW gives the
worker a hidden console of its own, which its children inherit, so none of them flash a
window. The worker is still unreachable from the terminal's Ctrl-C, and still survives the
terminal closing.

Stopping a worker also differs by OS. On POSIX, `request_stop` sends SIGTERM and the
worker's existing SIGTERM handler runs as always. On Windows there is no safe console
control event to send a detached worker (CTRL_BREAK_EVENT only reaches processes sharing
the sender's console, which this worker never does), so `request_stop`
instead writes a stop-request file next to the run. The worker's heartbeat thread polls
for that file on every beat and, on finding it, calls `_thread.interrupt_main(signal.SIGTERM)`
to run its SIGTERM handler in the main thread (Task 3 implements that polling)."""

import errno
import os
import shlex
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path, PurePath, PureWindowsPath

import psutil

IS_WINDOWS = os.name == "nt"
MISSING_BASH = (
    "Phil on Windows needs Git for Windows (it includes Git Bash): https://git-scm.com/download/win"
    " — or set [shell] bash to your bash.exe."
)


def pid_alive(pid: int) -> bool:
    """True if `pid` is a running process (a zombie counts as gone). Never signals it:
    psutil.pid_exists() falls back to os.kill(pid, 0) on POSIX, so this checks
    psutil.Process() directly instead, which reads OS process tables."""
    if pid <= 0:
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True  # alive, owned by someone else


def kill_tree(pid: int) -> None:
    """Kill every descendant of `pid`, then `pid` itself. Missing processes, and ones we may not
    kill, are skipped. On POSIX it waits only for the descendants, never the root: reaping the
    root is its parent's job (Popen.wait in shell.py), and waiting here would take its exit
    status away from Popen. A caller whose root isn't its own child (e.g. `phil stop` on a
    worker) polls `pid_alive` instead.

    On POSIX, when `pid` leads its own process group (as `start_new_session=True` makes it), the
    whole group is also sent SIGKILL. psutil only finds descendants through parent links, and a
    double-forked grandchild that was reparented to init has none: it would survive, holding the
    command's output pipes open. The group still holds it.

    Windows has no such group, so it sweeps for stragglers instead (`_kill_stragglers`)."""
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    if not IS_WINDOWS:
        try:
            # Never pid 1's group or Phil's own: killpg would signal every process in it.
            pgid = os.getpgid(pid)
            if pid > 1 and pgid == pid and pgid != os.getpgrp():
                os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    killed = [*children, root]
    for proc in killed:
        _kill(proc)
    if IS_WINDOWS:
        _kill_stragglers(killed)
    else:
        psutil.wait_procs(children, timeout=5)


def _kill(proc: psutil.Process) -> None:
    try:
        proc.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


# How many generations of stragglers _kill_stragglers chases, and how long it may take in all.
_STRAGGLER_ROUNDS = 5
_STRAGGLER_DEADLINE_S = 5.0


def _gone(proc: psutil.Process) -> bool:
    """Whether `proc` has exited. is_running() compares create times, so a pid since reused by
    another process counts as gone; a zombie counts as gone too. Never reaps."""
    try:
        return not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE
    except psutil.Error:
        return True


def _pid_reused(pid: int, started: float) -> bool:
    """Whether `pid` now names a live process other than the one that started at `started`."""
    try:
        return psutil.Process(pid).create_time() != started
    except psutil.Error:
        return False


def _kill_stragglers(killed: list[psutil.Process]) -> None:
    """Windows only: kill processes the tree was still starting when `kill_tree` ran.

    `children()` lists only the processes that existed at that moment. A parent killed while
    inside CreateProcess still creates its child, which then never runs (it was never resumed)
    but holds the command's inherited output pipes open forever. So, once the killed processes
    have exited, kill every process whose parent is one of them and that started after that
    parent; then do the same for those, until a round finds none. A process is never taken for
    a parent's child when that parent's pid already names another live process (Windows reuses
    pids, and the new owner's children are no straggler of ours), nor when it is older than the
    parent. All rounds together take at most _STRAGGLER_DEADLINE_S. The waits never take the
    root's exit status from its Popen."""
    deadline = time.monotonic() + _STRAGGLER_DEADLINE_S
    for _ in range(_STRAGGLER_ROUNDS):
        while not all(_gone(proc) for proc in killed) and time.monotonic() < deadline:
            time.sleep(0.02)
        started = {}
        for proc in killed:
            try:
                started[proc.pid] = proc.create_time()  # psutil caches it, so a dead one still has it
            except psutil.Error:
                pass
        snapshot = list(psutil.process_iter(["ppid"]))
        # Checked after the snapshot, so a pid reused before it was taken is caught.
        parents = {pid: when for pid, when in started.items() if not _pid_reused(pid, when)}
        stragglers = []
        for proc in snapshot:
            parent_started = parents.get(proc.info["ppid"])
            if parent_started is None or proc.pid in started:
                continue
            try:
                if proc.create_time() >= parent_started:
                    stragglers.append(proc)
            except psutil.Error:
                pass
        if not stragglers:
            return
        for proc in stragglers:
            _kill(proc)
        killed = stragglers
        if time.monotonic() >= deadline:
            return


def detach_kwargs() -> dict:
    """Popen keyword arguments that detach a child from Phil's console and process group.

    On Windows this is CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW, not DETACHED_PROCESS:
    DETACHED_PROCESS leaves the child with no console, so every console program it spawns
    (git, python, ...) opens its own visible console window. CREATE_NO_WINDOW instead gives
    the child a hidden console that its own children inherit, so nothing flashes on screen.
    The worker is still unreachable from the terminal's Ctrl-C, and still survives the
    terminal closing."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def request_stop(pid: int, stop_file: Path) -> None:
    """Ask a worker to stop cleanly. On POSIX, sends SIGTERM to `pid` (stop_file is
    ignored). On Windows, writes `pid` to `stop_file`: the worker's heartbeat thread notices
    it naming its own pid on its next poll and interrupts itself; no signal is sent.

    On both, a `pid` that isn't running raises ProcessLookupError (on POSIX, os.kill does)."""
    if IS_WINDOWS:
        if not pid_alive(pid):
            raise ProcessLookupError(errno.ESRCH, f"no process {pid}")
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(f"{pid}\n", encoding="utf-8")
    else:
        os.kill(pid, signal.SIGTERM)


# The error msvcrt.locking raises when LK_LOCK gives up. Windows' errno has it (36); macOS's
# doesn't, so tests of the Windows path can run anywhere.
_EDEADLOCK = getattr(errno, "EDEADLOCK", 36)


def lock_file(handle) -> None:
    """Block until an exclusive lock on `handle` is held. On Windows, msvcrt.locking(LK_LOCK)
    gives up with an OSError after about 10 one-second retries of its own, so this retries the
    call itself in a loop until it succeeds — the same indefinite blocking fcntl.flock gives on
    POSIX."""
    if IS_WINDOWS:
        import msvcrt

        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError as exc:
                # Only the lock-timeout error is retried; anything else (a bad handle) is real.
                if exc.errno != _EDEADLOCK:
                    raise
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


def _git_for_windows_root(git: PureWindowsPath) -> PureWindowsPath | None:
    """The Git for Windows install `git` belongs to, from its known layouts only:
    <Git>\\cmd\\git.exe, <Git>\\bin\\git.exe and <Git>\\mingw64\\bin\\git.exe. None otherwise."""
    folders = [parent.name.lower() for parent in git.parents]
    if folders[:2] == ["bin", "mingw64"]:
        return git.parents[2]
    if folders[:1] in (["cmd"], ["bin"]):
        return git.parents[1]
    return None


def find_bash(
    configured: str | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] = os.environ,
    exists: Callable[[object], bool] = os.path.exists,
) -> PurePath | None:
    """Git Bash's bash.exe (spec §4.3), or None. Never the System32 WSL launcher. Returns a
    `Path` on Windows, and the matched `PureWindowsPath` candidate elsewhere — so `str()` on
    the result is Windows-style on every OS."""
    candidates: list[PureWindowsPath] = []
    if configured:
        candidates.append(PureWindowsPath(configured))
    git = which("git")
    git_root = _git_for_windows_root(PureWindowsPath(git)) if git else None
    if git_root is not None:
        candidates.append(git_root / "bin" / "bash.exe")
    if environ.get("ProgramFiles"):
        candidates.append(PureWindowsPath(environ["ProgramFiles"], "Git", "bin", "bash.exe"))
    if environ.get("LocalAppData"):
        candidates.append(PureWindowsPath(environ["LocalAppData"], "Programs", "Git", "bin", "bash.exe"))
    for candidate in candidates:
        if not _is_wsl_launcher(candidate, environ) and exists(candidate):
            return Path(str(candidate)) if IS_WINDOWS else candidate
    return None


# The environment variable that carries an agent command to Git Bash on Windows.
SHELL_COMMAND_VAR = "PHIL_SHELL_COMMAND"
# A fixed script: it takes the command out of the environment, removes the variable so the
# command never sees it, and runs it.
_BASH_SCRIPT = f'set -- "${SHELL_COMMAND_VAR}"; unset {SHELL_COMMAND_VAR}; eval "$1"'


def windows_command(argv: list[str], bash: os.PathLike | str) -> tuple[list[str], dict[str, str]]:
    r"""(args, extra environment) that run the (already checked) `argv` through Git Bash.

    The command reaches bash in an environment variable, quoted with shlex.join, and never on
    bash's command line. Bash is an MSYS (Cygwin) program, and those parse a command line from
    a Windows parent by their own rules rather than the ones subprocess quotes for: inside
    quotes, `\\` becomes `\`, so `python -c "open('C:\\x')"` would arrive as `open('C:\x')`.
    The environment carries the text unchanged, and the script on the command line is fixed
    and has no backslash."""
    return [str(bash), "-c", _BASH_SCRIPT], {SHELL_COMMAND_VAR: shlex.join(argv)}
