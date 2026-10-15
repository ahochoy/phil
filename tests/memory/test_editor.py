import subprocess

import pytest

from phil.memory import editor
from phil.memory.editor import EditorError, edit_file


def fake_run(monkeypatch, returncode=0):
    calls = []

    def run(command, check):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(editor.subprocess, "run", run)
    return calls


def test_visual_wins_over_editor_and_takes_arguments(monkeypatch):
    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "nano")
    calls = fake_run(monkeypatch)
    edit_file("memory.md")
    assert calls == [["code", "--wait", "memory.md"]]


def test_editor_is_used_when_visual_is_unset(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "nano")
    calls = fake_run(monkeypatch)
    edit_file("memory.md")
    assert calls == [["nano", "memory.md"]]


def test_no_editor_found_raises(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(editor.sys, "platform", "linux")
    monkeypatch.setattr(editor.shutil, "which", lambda name: None)
    with pytest.raises(EditorError, match="no editor found"):
        edit_file("memory.md")


def test_a_failing_editor_raises(monkeypatch):
    monkeypatch.setenv("VISUAL", "nano")
    fake_run(monkeypatch, returncode=2)
    with pytest.raises(EditorError, match="status 2"):
        edit_file("memory.md")
