import re

from typer.testing import CliRunner

from phil.cli import main as cli
from phil.config import ROLES
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs

runner = CliRunner()


def test_phil_opens_the_chat_and_starts_a_run(calc_repo, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    spawned = []
    monkeypatch.setattr(cli, "spawn_worker", lambda root, run_id, mode, *a, **k: spawned.append((run_id, mode)))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="add subtract\ny\n")
    assert result.exit_code == 0, result.output
    assert "Phil · calc · base: main @" in result.output
    [record] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    assert spawned == [(record.run_id, "start")]


def test_phil_set_overrides_reach_the_chats_runs(calc_repo, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    result = runner.invoke(
        cli.app, ["--repo", str(calc_repo), "--set", "run.max_cost_usd=5"], input="add subtract\ny\n"
    )
    assert result.exit_code == 0, result.output
    [record] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    assert record.config_overrides == '["run.max_cost_usd=5"]'


def test_phil_rejects_a_bad_set_override(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--set", "run.nope=1"], input="")
    assert result.exit_code == 1
    assert "Invalid --set" in result.output


def test_chat_requires_chat_models(calc_repo):
    (calc_repo / "phil.toml").write_text('[models]\nimplementer = "ollama:test-model"\n')
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 1
    assert "No model for orchestrator (tier low)" in result.output
    assert "No model for architect (tier high)" in result.output
    assert "No model for critic (tier high)" in result.output


def test_chat_warns_about_uncommitted_files(calc_repo):
    (calc_repo / "scratch.txt").write_text("wip")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0
    assert "1 uncommitted file" in result.output


def test_chat_with_a_bad_base(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--base", "no-such-ref"], input="")
    assert result.exit_code == 1


def test_chat_reports_a_bad_agent_factory(calc_repo, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "nope:missing")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 1
    assert "nope" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_root_base_help_says_it_is_for_the_chat():
    result = runner.invoke(cli.app, ["--help"])
    assert "Chat only" in result.output


def test_chat_requires_the_provider_api_key(calc_repo, monkeypatch):
    (calc_repo / "phil.toml").write_text(
        "[models]\n" + "".join(f'{r} = "openrouter:openai/gpt-6-luna"\n' for r in ROLES)
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 1
    assert "openrouter needs OPENROUTER_API_KEY (used by" in result.output


def test_the_chat_key_check_passes_with_a_store_only_key(calc_repo, monkeypatch):
    from phil.key_store import set_key

    (calc_repo / "phil.toml").write_text(
        "[models]\n" + "".join(f'{r} = "openai:gpt-5-mini"\n' for r in ROLES)
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="add subtract\ny\n")
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" not in result.output


def test_chat_refuses_an_unknown_provider(calc_repo, monkeypatch):
    (calc_repo / "phil.toml").write_text('[models]\nhigh = "nowhere:x"\nlow = "ollama:test-model"\n')
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert 'Unknown provider "nowhere" in high model "nowhere:x".' in output
    assert "Add [providers.nowhere] to ~/.phil/config.toml or phil.toml." in output


def _approved_chat(calc_repo, monkeypatch) -> str:
    """Run a scripted chat to an approved, started run; return its chat id."""
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="add subtract\ny\n")
    assert result.exit_code == 0, result.output
    [chat] = (ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats").iterdir()
    return chat.name


def test_resume_an_unknown_chat(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", "c-nope"], input="")
    assert result.exit_code == 1
    assert "c-nope" in result.output
    missing = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", "c-20260101-000000"], input="")
    assert missing.exit_code == 1
    assert "c-20260101-000000" in missing.output


def test_resume_reopens_a_chat(calc_repo, monkeypatch):
    chat_id = _approved_chat(calc_repo, monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", chat_id], input="")
    assert result.exit_code == 0, result.output
    assert f"Reopened {chat_id}" in result.output


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class _FakeTerminal:
    """Stands in for TerminalIO under CliRunner (no real terminal): line IO plus a toolbar probe."""

    def __init__(self, toolbar):
        from phil.chat.terminal import LineIO

        self.toolbar = toolbar
        self.line = LineIO(cli.console)
        self.closed = False
        made.append(self)

    def width(self) -> int:
        return 80

    def chat_io(self, spawn):
        return self.line.chat_io(spawn)

    def run(self, fn):
        fn()
        self.toolbar_text = self.toolbar()

    def close(self):
        self.closed = True


made: list[_FakeTerminal] = []


def test_tty_lists_open_chats_and_reopens_one(calc_repo, monkeypatch):
    chat_id = _approved_chat(calc_repo, monkeypatch)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    made.clear()
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="1\n")
    assert result.exit_code == 0, result.output
    output = _plain(result.output)  # a TTY chat forces colour
    assert f"1. {chat_id} · " in output
    assert " · run r-" in output
    assert f"Reopened {chat_id}" in output
    [terminal] = made
    assert terminal.closed
    assert terminal.toolbar_text  # rendered from the controller's state


def test_tty_enter_starts_a_new_chat_and_new_skips_the_list(calc_repo, monkeypatch):
    chat_id = _approved_chat(calc_repo, monkeypatch)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="7\n\n")
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "choose 1–1" in output
    assert "Reopened" not in output
    fresh = runner.invoke(cli.app, ["--repo", str(calc_repo), "--new"], input="")
    assert fresh.exit_code == 0, fresh.output
    assert chat_id not in fresh.output
    assert "Open chats" not in fresh.output


def test_root_help_documents_resume_and_new():
    result = runner.invoke(cli.app, ["--help"], env={"COLUMNS": "200"})
    assert "Reopen a chat by id" in result.output
    assert "Start a new chat without listing open ones" in result.output


def test_eof_at_the_reopen_prompt_exits_cleanly(calc_repo, monkeypatch):
    _approved_chat(calc_repo, monkeypatch)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    made.clear()
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output  # EOF (Ctrl-D) at "Reopen one?" quits: no chat starts
    assert "Reopen one?" in _plain(result.output)
    assert made == []


def test_a_listed_chat_that_vanished_exits_cleanly(calc_repo, monkeypatch):
    from phil.chat import session as chat_session
    from phil.chat.session import ChatSummary

    gone = ChatSummary("c-20260101-000000", "old goal", "running", "r-gone", "running")
    monkeypatch.setattr(chat_session, "list_open_chats", lambda paths, conn: [gone])
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="1\n")
    assert result.exit_code == 1
    assert "cannot reopen" in _plain(result.output)
    assert "c-20260101-000000" in _plain(result.output)


def test_only_decimal_digits_choose_a_chat(calc_repo, monkeypatch):
    _approved_chat(calc_repo, monkeypatch)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="¹\n\n")
    assert result.exit_code == 0, result.output
    assert "choose 1–1" in _plain(result.output)
    assert "Reopened" not in _plain(result.output)


def test_a_chat_open_in_another_window_is_refused(calc_repo, monkeypatch):
    import os

    chat_id = _approved_chat(calc_repo, monkeypatch)
    lock = ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats" / chat_id / "chat.lock"
    assert not lock.exists()  # released when the first chat ended
    lock.write_text(str(os.getppid()))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", chat_id], input="")
    assert result.exit_code == 1
    assert f"Chat {chat_id} is already open in another window (pid {os.getppid()})." in " ".join(result.output.split())
    assert "Reopened" not in result.output


def test_a_stale_chat_lock_is_taken_over_and_released(calc_repo, monkeypatch):
    import subprocess
    import sys

    chat_id = _approved_chat(calc_repo, monkeypatch)
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    lock = ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats" / chat_id / "chat.lock"
    lock.write_text(str(process.pid))
    seen = {}
    from phil.chat.controller import ChatController

    original = ChatController.run

    def run(self):
        seen["lock"] = lock.read_text().strip()
        seen["propagate"] = __import__("logging").getLogger("phil").propagate
        return original(self)

    monkeypatch.setattr(ChatController, "run", run)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", chat_id], input="")
    assert result.exit_code == 0, result.output
    assert seen == {"lock": str(__import__("os").getpid()), "propagate": False}
    assert not lock.exists()


def test_the_open_chats_list_marks_chats_open_elsewhere(calc_repo, monkeypatch):
    import os

    chat_id = _approved_chat(calc_repo, monkeypatch)
    lock = ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats" / chat_id / "chat.lock"
    lock.write_text(str(os.getppid()))
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="1\n")
    output = " ".join(_plain(result.output).split())
    assert f"1. {chat_id} · " in output and "open elsewhere" in output
    assert result.exit_code == 1
    assert "already open in another window" in output
