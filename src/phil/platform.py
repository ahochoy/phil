"""Every OS difference Phil depends on, in one place (spec 2026-10-07). POSIX keeps the
behaviour Phil always had; Windows runs agent commands through Git Bash and controls
processes with psutil. Never use os.kill(pid, 0) to probe a process: on Windows signal 0 is
CTRL_C_EVENT, which interrupts the whole console.

Stopping a worker also differs by OS. On POSIX, `request_stop` sends SIGTERM and the
worker's existing SIGTERM handler runs as always. On Windows there is no safe console
control event to send a detached worker (CTRL_BREAK_EVENT only reaches processes sharing
the sender's console, which a DETACHED_PROCESS worker never does), so `request_stop`
instead writes a stop-request file next to the run. The worker's heartbeat thread polls
for that file on every beat and, on finding it, calls `_thread.interrupt_main(signal.SIGTERM)`
to run its SIGTERM handler in the main thread (Task 3 implements that polling)."""

import os
import shlex
import shutil
import signal
import subprocess
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
    kill, are skipped. Waits only for the descendants, never the root: reaping the root is its
    parent's job (Popen.wait in shell.py), and waiting here would take its exit status away
    from Popen. A caller whose root isn't its own child (e.g. `phil stop` on a worker) polls
    `pid_alive` instead."""
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for proc in [*children, root]:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(children, timeout=5)


def detach_kwargs() -> dict:
    """Popen keyword arguments that detach a child from Phil's console and process group."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def request_stop(pid: int, stop_file: Path) -> None:
    """Ask a worker to stop cleanly. On POSIX, sends SIGTERM to `pid` (stop_file is
    ignored). On Windows, writes `stop_file` so the worker's heartbeat thread notices it
    on its next poll and interrupts itself; no signal is sent."""
    if IS_WINDOWS:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text("stop\n", encoding="utf-8")
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
) -> PurePath | None:
    """Git Bash's bash.exe (spec §4.3), or None. Never the System32 WSL launcher. Returns a
    `Path` on Windows, and the matched `PureWindowsPath` candidate elsewhere — so `str()` on
    the result is Windows-style on every OS."""
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
            return Path(str(candidate)) if IS_WINDOWS else candidate
    return None


def windows_argv(argv: list[str], bash: os.PathLike | str) -> list[str]:
    """The argv Windows runs: bash -c with every (already checked) argument quoted."""
    return [str(bash), "-c", shlex.join(argv)]
