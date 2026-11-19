import subprocess

import pytest

from phil.ui.screen import clipboard


def _commands(monkeypatch, on_path: set[str], failing: frozenset[str] = frozenset()):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        name = args[0].rsplit("/", 1)[-1]
        return subprocess.CompletedProcess(args, 1 if name in failing else 0)

    monkeypatch.setattr(clipboard, "_which", lambda name: f"/bin/{name}" if name in on_path else None)
    monkeypatch.setattr(clipboard, "_run", run)
    return calls


@pytest.mark.parametrize(
    "platform, on_path, expected",
    [
        ("darwin", {"pbcopy"}, ["/bin/pbcopy"]),
        ("win32", {"clip.exe"}, ["/bin/clip.exe"]),
        ("linux", {"wl-copy", "xclip"}, ["/bin/wl-copy"]),
        ("linux", {"xclip"}, ["/bin/xclip", "-selection", "clipboard"]),
    ],
)
def test_the_platforms_command_gets_the_text_on_stdin(monkeypatch, platform, on_path, expected):
    calls = _commands(monkeypatch, on_path)
    assert clipboard.system_copy("héllo", platform) is True
    [(args, kwargs)] = calls
    assert args == expected
    assert kwargs["input"] == ("héllo".encode("utf-16") if platform == "win32" else "héllo".encode())
    assert not kwargs.get("shell") and 0 < kwargs["timeout"] <= 5


def test_no_command_on_path_is_false(monkeypatch):
    calls = _commands(monkeypatch, set())
    assert clipboard.system_copy("x", "linux") is False
    assert calls == []


def test_a_failing_command_falls_through_to_the_next(monkeypatch):
    calls = _commands(monkeypatch, {"wl-copy", "xclip"}, failing=frozenset({"wl-copy"}))
    assert clipboard.system_copy("x", "linux") is True
    assert [args[0] for args, _ in calls] == ["/bin/wl-copy", "/bin/xclip"]


def test_every_command_failing_is_false(monkeypatch):
    _commands(monkeypatch, {"wl-copy", "xclip"}, failing=frozenset({"wl-copy", "xclip"}))
    assert clipboard.system_copy("x", "linux") is False


def test_a_command_that_cannot_run_or_times_out_is_false(monkeypatch):
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(clipboard, "_which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(clipboard, "_run", run)
    assert clipboard.system_copy("x", "darwin") is False
    monkeypatch.setattr(clipboard, "_run", lambda args, **kwargs: (_ for _ in ()).throw(OSError("no")))
    assert clipboard.system_copy("x", "darwin") is False
