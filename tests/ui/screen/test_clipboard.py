import os
import shutil
import subprocess
import sys

import pytest

from phil.ui.screen import clipboard


def _recording_run(monkeypatch, failing: frozenset[str] = frozenset()):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        name = os.path.basename(args[0])
        return subprocess.CompletedProcess(args, 1 if name in failing else 0)

    monkeypatch.setattr(clipboard, "_run", run)
    return calls


def _commands(monkeypatch, on_path: set[str], failing: frozenset[str] = frozenset()):
    """Each name in `on_path` resolves to /usr/bin/<name>, outside the cwd and any repository."""
    monkeypatch.setattr(clipboard, "_which", lambda name, path=None: f"/usr/bin/{name}" if name in on_path else None)
    return _recording_run(monkeypatch, failing)


@pytest.mark.parametrize(
    "platform, on_path, expected",
    [
        ("darwin", {"pbcopy"}, ["/usr/bin/pbcopy"]),
        ("linux", {"wl-copy", "xclip"}, ["/usr/bin/wl-copy"]),
        ("linux", {"xclip"}, ["/usr/bin/xclip", "-selection", "clipboard"]),
    ],
)
def test_the_platforms_command_gets_the_text_on_stdin(monkeypatch, platform, on_path, expected):
    calls = _commands(monkeypatch, on_path)
    assert clipboard.system_copy("héllo", platform) is True
    [(args, kwargs)] = calls
    assert args == expected
    assert kwargs["input"] == "héllo".encode()
    assert not kwargs.get("shell") and 0 < kwargs["timeout"] <= 5


def test_windows_uses_system32_clip_by_its_absolute_path(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"D:\Win")
    expected = os.path.join(r"D:\Win", "System32", "clip.exe")
    monkeypatch.setattr(clipboard, "_isfile", lambda path: path == expected)
    monkeypatch.setattr(clipboard, "_which", lambda name, path=None: "planted\\clip.exe")  # never consulted
    calls = _recording_run(monkeypatch)
    assert clipboard.system_copy("héllo", "win32") is True
    [(args, kwargs)] = calls
    assert args == [expected]
    assert kwargs["input"] == "héllo".encode("utf-16")


def test_windows_without_system32_clip_copies_nothing(monkeypatch):
    monkeypatch.setattr(clipboard, "_isfile", lambda path: False)
    calls = _recording_run(monkeypatch)
    assert clipboard.system_copy("x", "win32") is False
    assert calls == []


@pytest.fixture
def planted(tmp_path, monkeypatch):
    """A `pbcopy` and a `wl-copy` planted in the current directory (a repository, say)."""
    monkeypatch.setattr(clipboard, "_which", shutil.which)  # the real lookup
    for name in ("pbcopy", "wl-copy"):
        tool = tmp_path / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PATH lookup")
@pytest.mark.parametrize("entry", ["", ".", "relative/dir", "cwd"])
def test_a_tool_planted_in_the_current_directory_is_ignored(planted, monkeypatch, entry):
    path_entry = str(planted) if entry == "cwd" else entry
    if entry == "relative/dir":
        (planted / "relative" / "dir").mkdir(parents=True)
        for name in ("pbcopy", "wl-copy"):
            os.link(planted / name, planted / "relative" / "dir" / name)
    monkeypatch.setenv("PATH", path_entry)
    calls = _recording_run(monkeypatch)
    platform = "darwin" if sys.platform == "darwin" else "linux"
    assert clipboard.system_copy("x", platform) is False
    assert calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PATH lookup")
def test_a_tool_inside_a_distrusted_repository_is_ignored(tmp_path, monkeypatch):
    repo, elsewhere = tmp_path / "repo", tmp_path / "elsewhere"
    (repo / "bin").mkdir(parents=True)
    elsewhere.mkdir()
    tool = repo / "bin" / "pbcopy"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(clipboard, "_which", shutil.which)
    monkeypatch.setattr(clipboard, "_distrusted", [])
    monkeypatch.setenv("PATH", str(repo / "bin"))
    calls = _recording_run(monkeypatch)
    assert clipboard.system_copy("x", "darwin") is True       # not distrusted yet
    clipboard.distrust(repo)
    assert clipboard.system_copy("x", "darwin") is False
    assert len(calls) == 1


def test_no_command_on_path_is_false(monkeypatch):
    calls = _commands(monkeypatch, set())
    assert clipboard.system_copy("x", "linux") is False
    assert calls == []


def test_a_failing_command_falls_through_to_the_next(monkeypatch):
    calls = _commands(monkeypatch, {"wl-copy", "xclip"}, failing=frozenset({"wl-copy"}))
    assert clipboard.system_copy("x", "linux") is True
    assert [args[0] for args, _ in calls] == ["/usr/bin/wl-copy", "/usr/bin/xclip"]


def test_every_command_failing_is_false(monkeypatch):
    _commands(monkeypatch, {"wl-copy", "xclip"}, failing=frozenset({"wl-copy", "xclip"}))
    assert clipboard.system_copy("x", "linux") is False


def test_a_command_that_cannot_run_or_times_out_is_false(monkeypatch):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    def unrunnable(args, **kwargs):
        raise OSError("no")

    _commands(monkeypatch, {"pbcopy"})
    monkeypatch.setattr(clipboard, "_run", timeout)
    assert clipboard.system_copy("x", "darwin") is False
    monkeypatch.setattr(clipboard, "_run", unrunnable)
    assert clipboard.system_copy("x", "darwin") is False
