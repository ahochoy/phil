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


def test_kill_tree_leaves_the_root_for_its_parent_to_reap():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    platform.kill_tree(proc.pid)
    returncode = proc.wait(timeout=10)
    assert returncode != 0
    if not platform.IS_WINDOWS:
        assert returncode == -9


def test_kill_tree_skips_a_process_it_may_not_kill(monkeypatch):
    import psutil

    killed = []

    class Proc:
        def __init__(self, pid, denied):
            self.pid, self.denied = pid, denied

        def children(self, recursive=False):
            return [Proc(2, denied=True), Proc(3, denied=False)]

        def kill(self):
            if self.denied:
                raise psutil.AccessDenied(self.pid)
            killed.append(self.pid)

    monkeypatch.setattr(psutil, "Process", lambda pid: Proc(pid, denied=False))
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: ([], []))
    platform.kill_tree(1)
    assert killed == [3, 1]


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


def test_find_bash_caps_the_git_derived_walk_at_three_parents():
    git = r"C:\a\b\c\d\git.exe"
    bash = str(PureWindowsPath(r"C:\bin", "bash.exe"))
    found = platform.find_bash(
        None, which=lambda n: git if n == "git" else None, environ={}, exists=lambda p: str(p) == bash
    )
    assert found is None


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


def test_lock_file_retries_past_msvcrt_lk_lock_giving_up():
    # msvcrt.locking(LK_LOCK) gives up with OSError after ~10 one-second retries of its own;
    # lock_file must retry past that to block the way fcntl.flock does. Not observable on
    # macOS (no msvcrt) beyond the round trip above, so this only runs on Windows.
    if not platform.IS_WINDOWS:
        pytest.skip("msvcrt behaviour isn't observable on macOS")


def test_detach_kwargs_on_posix():
    if platform.IS_WINDOWS:
        pytest.skip("POSIX shape")
    assert platform.detach_kwargs() == {"start_new_session": True}


def test_detach_kwargs_on_windows_uses_create_no_window_not_detached_process(monkeypatch):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    kwargs = platform.detach_kwargs()
    # Exactly these two flags: DETACHED_PROCESS (0x8) is not one of them.
    assert kwargs == {"creationflags": 0x200 | 0x08000000}


def test_request_stop_sends_sigterm_on_posix(tmp_path):
    import signal

    if platform.IS_WINDOWS:
        pytest.skip("POSIX shape")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        platform.request_stop(proc.pid, tmp_path / "stop-requested")
        assert proc.wait(timeout=10) == -signal.SIGTERM
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_request_stop_writes_a_file_on_windows_and_never_signals(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "kill", lambda *a: (_ for _ in ()).throw(AssertionError("os.kill called")))
    stop_file = tmp_path / "run" / "stop-requested"
    platform.request_stop(4242, stop_file)
    assert stop_file.read_text(encoding="utf-8") == "4242\n"
