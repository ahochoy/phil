import os
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


@pytest.fixture(autouse=True)
def outside_cwd(monkeypatch):
    # The fake programs below have Windows-style paths, which a POSIX Path would read as inside the cwd.
    monkeypatch.setattr(editor, "_inside_cwd", lambda found: False)


def installed(monkeypatch, **programs):
    """Make shutil.which find exactly these programs: a lookup matches a program when it is the name or
    ends in it after a separator (the Windows lookup asks about `<PATH entry>/<name>`). Returns the
    names it was asked about."""
    searched = []

    def which(name):
        searched.append(name)
        for key, found in programs.items():
            if name == key or (name.endswith(key) and name[-len(key) - 1] in "/\\"):
                return found
        return None

    monkeypatch.setattr(editor.shutil, "which", which)
    return searched


def windows(monkeypatch, on=True):
    monkeypatch.setattr(editor, "_is_windows", lambda: on)


def test_visual_wins_over_editor_and_takes_arguments(monkeypatch):
    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "nano")
    installed(monkeypatch, code="/usr/bin/code", nano="/usr/bin/nano")
    windows(monkeypatch, False)
    calls = fake_run(monkeypatch)
    edit_file("memory.md")
    assert calls == [["/usr/bin/code", "--wait", "memory.md"]]


def test_editor_is_used_when_visual_is_unset(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "nano")
    installed(monkeypatch, nano="/usr/bin/nano")
    windows(monkeypatch, False)
    calls = fake_run(monkeypatch)
    edit_file("memory.md")
    assert calls == [["/usr/bin/nano", "memory.md"]]


def test_no_editor_found_raises(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    windows(monkeypatch, False)
    installed(monkeypatch)
    with pytest.raises(EditorError, match="no editor found"):
        edit_file("memory.md")


def test_a_failing_editor_raises(monkeypatch):
    monkeypatch.setenv("VISUAL", "nano")
    installed(monkeypatch, nano="/usr/bin/nano")
    windows(monkeypatch, False)
    fake_run(monkeypatch, returncode=2)
    with pytest.raises(EditorError, match="status 2"):
        edit_file("memory.md")


def test_an_editor_that_is_not_installed_is_named(monkeypatch):
    monkeypatch.setenv("VISUAL", "nosuch --flag")
    windows(monkeypatch, False)
    installed(monkeypatch)
    calls = fake_run(monkeypatch)
    with pytest.raises(EditorError, match="nosuch"):
        edit_file("memory.md")
    assert calls == []


def test_a_launch_failure_raises(monkeypatch):
    monkeypatch.setenv("VISUAL", "nano")
    installed(monkeypatch, nano="/usr/bin/nano")
    windows(monkeypatch, False)

    def run(command, check):
        raise PermissionError("denied")

    monkeypatch.setattr(editor.subprocess, "run", run)
    with pytest.raises(EditorError, match="can't start nano: denied"):
        edit_file("memory.md")


def test_unbalanced_quotes_raise(monkeypatch):
    monkeypatch.setenv("VISUAL", '"unterminated --wait')
    for on in (False, True):
        windows(monkeypatch, on)
        with pytest.raises(EditorError, match=r"\$VISUAL can't be read"):
            edit_file("memory.md")


def test_windows_quoted_path_with_an_argument(monkeypatch):
    path = r"C:\Program Files\Notepad++\notepad++.exe"
    monkeypatch.setenv("VISUAL", f'"{path}" -multiInst')
    windows(monkeypatch)
    installed(monkeypatch, **{path: path})
    calls = fake_run(monkeypatch)
    edit_file(r"C:\memory.md")
    assert calls == [[path, "-multiInst", r"C:\memory.md"]]


def test_windows_bare_command_resolves_to_its_shim(monkeypatch):
    shim = r"C:\Users\me\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd"
    monkeypatch.setenv("VISUAL", "code --wait")
    windows(monkeypatch)
    installed(monkeypatch, code=shim)
    calls = fake_run(monkeypatch)
    edit_file(r"C:\memory.md")
    assert calls == [[shim, "--wait", r"C:\memory.md"]]


def test_windows_falls_back_to_notepad(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    windows(monkeypatch)
    installed(monkeypatch, notepad=r"C:\Windows\notepad.exe")
    calls = fake_run(monkeypatch)
    edit_file(r"C:\memory.md")
    assert calls == [[r"C:\Windows\notepad.exe", r"C:\memory.md"]]


SHIM = r"C:\x\code.cmd"


@pytest.mark.parametrize("bad", ["&", "|", "<", ">", "^", "%", "!", '"', "\n"])
def test_a_batch_file_editor_refuses_cmd_metacharacters_in_the_path(monkeypatch, bad):
    monkeypatch.setenv("VISUAL", "code --wait")
    windows(monkeypatch)
    installed(monkeypatch, code=SHIM)
    calls = fake_run(monkeypatch)
    with pytest.raises(EditorError, match=r"won't start code \(a batch file\) with this path"):
        edit_file(f"C:\\repos\\a{bad}b\\memory.md")
    assert calls == []


def test_a_batch_file_editor_refuses_metacharacters_in_its_own_arguments(monkeypatch):
    monkeypatch.setenv("VISUAL", "code --wait&calc")
    windows(monkeypatch)
    installed(monkeypatch, code=SHIM)
    calls = fake_run(monkeypatch)
    with pytest.raises(EditorError, match="a batch file"):
        edit_file(r"C:\memory.md")
    assert calls == []


def test_a_batch_file_editor_launches_with_a_clean_path(monkeypatch):
    monkeypatch.setenv("VISUAL", "code --wait")
    windows(monkeypatch)
    installed(monkeypatch, code=SHIM.upper())
    calls = fake_run(monkeypatch)
    edit_file(r"C:\repos\calc\memory.md")
    assert calls == [[SHIM.upper(), "--wait", r"C:\repos\calc\memory.md"]]


def test_an_exe_may_have_metacharacters_in_the_path(monkeypatch):
    monkeypatch.setenv("VISUAL", "notepad")
    windows(monkeypatch)
    installed(monkeypatch, notepad=r"C:\Windows\notepad.exe")
    calls = fake_run(monkeypatch)
    edit_file(r"C:\a&b\memory.md")
    assert len(calls) == 1


def test_windows_searches_path_without_the_current_folder(monkeypatch):
    monkeypatch.setenv("VISUAL", "code")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "bin1", " ", "bin2"]))
    windows(monkeypatch)
    searched = installed(monkeypatch, **{os.path.join("bin2", "code"): r"C:\bin2\code.exe"})
    fake_run(monkeypatch)
    edit_file(r"C:\memory.md")
    assert searched == [os.path.join("bin1", "code"), os.path.join("bin2", "code")]


def test_a_lookup_on_windows_never_reaches_a_planted_editor_in_the_current_folder(monkeypatch, tmp_path):
    monkeypatch.undo()  # drop the autouse stub so the real lookup and cwd check run
    on_windows = os.name == "nt"
    filename = "phil_fake_editor.cmd" if on_windows else "phil_fake_editor"
    repo, bindir = tmp_path / "repo", tmp_path / "bin"
    for folder in (repo, bindir):
        folder.mkdir()
        stub = folder / filename
        stub.write_text("@echo off\n" if on_windows else "#!/bin/sh\n", encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(bindir)]))
    monkeypatch.setenv("VISUAL", "phil_fake_editor")
    monkeypatch.setattr(editor, "_is_windows", lambda: True)  # the Windows lookup, on any OS
    calls = fake_run(monkeypatch)
    edit_file("memory.md")
    assert [os.path.normcase(os.path.abspath(call)) for call in calls[0][:1]] == [
        os.path.normcase(os.path.abspath(bindir / filename))
    ]


def test_a_symlink_in_the_current_folder_to_an_outside_editor_is_refused(monkeypatch, tmp_path):
    monkeypatch.undo()
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside_editor"
    outside.write_text("", encoding="utf-8")
    link = repo / "myeditor"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks aren't supported here")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("VISUAL", "myeditor")
    monkeypatch.setattr(editor.shutil, "which", lambda name: str(link))
    calls = fake_run(monkeypatch)
    with pytest.raises(EditorError, match="from the current folder"):
        edit_file("memory.md")
    assert calls == []


def test_an_editor_found_in_the_current_folder_is_refused(monkeypatch, tmp_path):
    monkeypatch.undo()  # drop the autouse stub so the real check runs
    fake = tmp_path / "myeditor"
    fake.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISUAL", "myeditor --wait")
    monkeypatch.setattr(editor.shutil, "which", lambda name, path=None: str(fake))
    calls = fake_run(monkeypatch)
    with pytest.raises(EditorError, match="won't start myeditor from the current folder; set \\$EDITOR to a full path"):
        edit_file("memory.md")
    assert calls == []


def test_an_explicit_absolute_path_is_allowed_in_the_current_folder(monkeypatch, tmp_path):
    monkeypatch.undo()
    fake = tmp_path / "myeditor"
    fake.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VISUAL", str(fake))
    monkeypatch.setattr(editor.shutil, "which", lambda name, path=None: name)
    calls = fake_run(monkeypatch)
    edit_file("memory.md")
    assert calls == [[str(fake), "memory.md"]]
