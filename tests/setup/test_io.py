import builtins

import pytest

from phil.setup.io import ScriptedSetupIO, SetupCancelled, TerminalSetupIO
from phil.ui.theme import make_console


def terminal(monkeypatch, answers):
    replies = list(answers)
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(builtins, "input", fake_input)
    console = make_console(width=200)
    return TerminalSetupIO(console, interactive=False), console, prompts


def test_plain_input_without_a_terminal(monkeypatch, capsys):
    io, _, prompts = terminal(monkeypatch, ["", "typed", "x", "3", "2", "sk-TESTSECRET-123"])
    assert io.ask("high model", default="a:b") == "a:b"
    assert io.ask("high model", default="a:b") == "typed"
    assert io.choose("Pick", ["one", "two", "three"]) == 2  # "x" is refused, then 3
    assert io.choose("Pick", ["one", "two"], default=0) == 1
    assert io.secret("KEY") == "sk-TESTSECRET-123"
    assert prompts[0] == "high model [a:b]: "
    out = capsys.readouterr().out
    assert "Enter a number from 1 to 3." in out
    assert "sk-TESTSECRET-123" not in out


def test_printed_text_is_escaped(monkeypatch, capsys):
    io, _, _ = terminal(monkeypatch, [])
    io.say("[bold]not markup[/bold]")
    assert "[bold]not markup[/bold]" in capsys.readouterr().out


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), EOFError()])
def test_ctrl_c_and_eof_cancel(monkeypatch, interrupt):
    io, _, _ = terminal(monkeypatch, [interrupt, interrupt, interrupt])
    with pytest.raises(SetupCancelled):
        io.ask("x")
    with pytest.raises(SetupCancelled):
        io.choose("x", ["a"])
    with pytest.raises(SetupCancelled):
        io.secret("x")


def test_a_terminal_reads_secrets_with_getpass(monkeypatch):
    monkeypatch.setattr("phil.setup.io.getpass.getpass", lambda prompt: "sk-TESTSECRET-123")
    io = TerminalSetupIO(make_console(), interactive=True)
    assert io.secret("KEY") == "sk-TESTSECRET-123"


def test_scripted_io_runs_out_into_a_cancel():
    io = ScriptedSetupIO(["2", "Thr"])
    assert io.choose("Pick", ["one", "two", "three"]) == 1
    assert io.choose("Pick", ["one", "two", "three"]) == 2
    with pytest.raises(SetupCancelled):
        io.ask("more")
    assert io.lines[0] == "Pick"
