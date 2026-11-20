import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from phil.cli import main as cli
from phil.config import ROLES
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from tests.helpers import classic_repo

runner = CliRunner()


def _screen_setting(home: Path, value: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f'[chat]\nscreen = "{value}"\n', encoding="utf-8")


@pytest.fixture(autouse=True)
def inline_screen(phil_home):
    """These tests drive the inline chat (`_terminal`) unless a test opts into the full screen."""
    _screen_setting(phil_home, "inline")


def test_phil_opens_the_chat_and_starts_a_run(calc_repo, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    spawned = []
    monkeypatch.setattr(cli, "spawn_worker", lambda root, run_id, mode, *a, **k: spawned.append((run_id, mode)))
    classic_repo(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="add subtract\ny\n")
    assert result.exit_code == 0, result.output
    assert "calc @ main " in result.output
    [record] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    assert spawned == [(record.run_id, "start")]


def test_phil_set_overrides_reach_the_chats_runs(calc_repo, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    classic_repo(calc_repo)
    result = runner.invoke(
        cli.app, ["--repo", str(calc_repo), "--set", "run.max_cost_usd=5"], input="add subtract\ny\n"
    )
    assert result.exit_code == 0, result.output
    [record] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    # The chat's own override first, then the pin of the setup command it showed (none here).
    assert json.loads(record.config_overrides) == ["run.max_cost_usd=5", 'project.setup_cmd=""']


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
    assert "⚠ 1 uncommitted (not included in runs)" in result.output


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
    # The goal's price check consults the process-wide price list: an empty one, never the network.
    from phil.agents.pricing import PriceBook

    empty = PriceBook(calc_repo.parent / "prices.json", fetch=lambda: {"data": []})
    monkeypatch.setattr("phil.agents.invoke._default_prices", empty)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="add subtract\ny\n")
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" not in result.output


def test_a_legacy_six_role_config_starts_the_chat(calc_repo):
    legacy = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")
    (calc_repo / "phil.toml").write_text("[models]\n" + "".join(f'{r} = "ollama:test-model"\n' for r in legacy))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "No model for" not in result.output


def test_the_chat_checks_the_key_of_an_answerer_model(calc_repo, monkeypatch):
    others = "".join(f'{r} = "ollama:test-model"\n' for r in ROLES if r != "answerer")
    (calc_repo / "phil.toml").write_text("[models]\n" + others + 'answerer = "openrouter:openai/gpt-6-luna"\n')
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 1
    assert "openrouter needs OPENROUTER_API_KEY (used by answerer)" in " ".join(result.output.split())


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
    classic_repo(calc_repo)
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

    def __init__(self, toolbar, live_row=None):
        from phil.chat.terminal import LineIO

        self.toolbar = toolbar
        self.live_row = live_row
        self.line = LineIO(cli.console)
        self.closed = False
        made.append(self)

    def width(self) -> int:
        return 80

    def chat_io(self, spawn):
        return self.line.chat_io(spawn)

    def run(self, fn):
        from phil.ui.toolbar import toolbar_text

        fn()
        self.toolbar_text = toolbar_text(self.toolbar())

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
    [run] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    assert f"1. {run.name} · run " in output  # label · run state · goal; no chat id
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
    fresh_output = _plain(fresh.output)
    assert f"1. {chat_id}" not in fresh_output  # no picker; the banner may still mention the open chat
    assert "Reopen one?" not in fresh_output
    assert "Open chats" not in fresh_output


def test_root_help_documents_resume_and_new():
    result = runner.invoke(cli.app, ["--help"], env={"COLUMNS": "200"})
    # CI forces colour (GitHub Actions): Rich styles the option's dashes apart, so compare plain text.
    output = _plain(result.output)
    assert "Reopen a chat by id" in output
    assert "Start a new chat (the default)." in output
    assert "--continue" in output


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
    lock.write_text(str(os.getppid()), encoding="utf-8")
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
    lock.write_text(str(process.pid), encoding="utf-8")
    seen = {}
    from phil.chat.controller import ChatController

    original = ChatController.run

    def run(self):
        import logging

        seen["lock"] = lock.read_text(encoding="utf-8").strip()
        # The chat's diagnostics go to its own phil.log, through a handler on the root logger.
        seen["log"] = [
            Path(h.baseFilename).name
            for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler) and Path(h.baseFilename).parent == lock.parent
        ]
        return original(self)

    monkeypatch.setattr(ChatController, "run", run)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", chat_id], input="")
    assert result.exit_code == 0, result.output
    assert seen == {"lock": str(__import__("os").getpid()), "log": ["phil.log"]}
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
    [run] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    assert f"1. {run.name} · run " in output and "open elsewhere" in output
    assert result.exit_code == 1
    assert "already open in another window" in output


# --- the full-screen chat, `phil chat` and `--continue` ------------------------------------------


def test_exit_line():
    assert cli._exit_line("c-1", "approval", set()) == "To continue this chat:  phil --resume c-1"
    assert cli._exit_line("c-1", "idle", {"c-1"}) == "To continue this chat:  phil --resume c-1"
    assert cli._exit_line("c-1", "idle", set()) is None


def _item_text(item) -> str:
    if hasattr(item, "plain"):
        return item.plain
    from rich.console import Console

    from phil.ui.theme import PHIL_THEME

    console = Console(file=io.StringIO(), record=True, width=80, theme=PHIL_THEME)
    console.print(item)  # a renderable, such as the banner drawn at the pane's width
    return console.export_text()


class _FakeScreen:
    """Stands in for ScreenIO under CliRunner: runs `fn` inline, reads lines like LineIO, and records
    the posted items, the header, cleared conversations and discarded interrupts."""

    def __init__(self, view):
        from rich.console import Console

        from phil.chat.terminal import LineIO
        from phil.ui.theme import PHIL_THEME

        self.view = view
        self.app = self
        self.items: list = []
        self.headers: list[tuple[str, str, str]] = []
        self.cleared = 0
        self.discarded = 0
        self.closed = False
        self.console = Console(file=io.StringIO(), record=True, width=80, theme=PHIL_THEME)
        self.line = LineIO(cli.console)
        screens.append(self)

    def width(self) -> int:
        return 80

    def chat_io(self, spawn):
        return self.line.chat_io(spawn)

    def set_view(self, view):
        self.view = view

    def discard_interrupt(self):
        self.discarded += 1

    def post_item(self, renderable):
        self.items.append(renderable)

    def set_header(self, repo, branch, chat_id):
        self.headers.append((repo, branch, chat_id))

    def clear_conversation(self):
        self.cleared += 1

    def run(self, fn):
        return fn()

    def close(self):
        self.closed = True

    def text(self) -> str:
        return "\n".join(_item_text(item) for item in self.items) + "\n" + self.console.export_text()


screens: list[_FakeScreen] = []
FULL_ENV = {"COLUMNS": "100", "LINES": "30"}


def _no_terminal(*args, **kwargs):
    raise AssertionError("the full-screen chat must not use the inline terminal")


def _full(monkeypatch, phil_home) -> None:
    _screen_setting(phil_home, "full")
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    # On Windows the legacy console falls back to inline; these tests stand in Windows Terminal.
    monkeypatch.setenv("WT_SESSION", "phil-tests")
    monkeypatch.setattr(cli, "_screen", _FakeScreen)
    monkeypatch.setattr(cli, "_terminal", _no_terminal)
    screens.clear()


def _listed_chat(paths: ProjectPaths, second: int) -> str:
    """An open chat (its run isn't done) that only needs listing, not reopening."""
    from phil.chat.session import ChatSession

    chat = ChatSession.create(paths, now=lambda: datetime(2026, 10, 8, 12, 0, second))
    chat.save_state({"run_id": f"r-90{second:02d}", "done_seen": False})
    return chat.id


def _two_chats(calc_repo, monkeypatch) -> tuple[str, str]:
    """Two scripted chats with started runs; (older, newer) by when they were last active."""
    from phil.chat.session import list_open_chats

    _approved_chat(calc_repo, monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="add subtract\ny\n")
    assert result.exit_code == 0, result.output
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    newer, older = (chat.id for chat in list_open_chats(paths, connect(paths.db_path)))
    return older, newer


def _chat_ids(calc_repo) -> set[str]:
    return {d.name for d in (ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats").iterdir()}


def test_full_start_posts_the_banner_and_the_open_chats_hint(calc_repo, monkeypatch, phil_home):
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    _listed_chat(paths, 1)
    _listed_chat(paths, 2)
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    [screen] = screens
    texts = [_item_text(item) for item in screen.items]
    assert any("Phil v" in text for text in texts)
    assert "2 open chats · /chat" in texts
    assert texts.index("2 open chats · /chat") > max(i for i, t in enumerate(texts) if "Phil v" in t)
    assert "Reopen one?" not in _plain(result.output) and "Open chats:" not in _plain(result.output)
    [new_chat] = _chat_ids(calc_repo) - {f"c-20261008-12000{n}" for n in (1, 2)}
    assert screen.headers[0] == ("calc", "main", new_chat)
    assert screen.closed


def test_the_open_chats_hint_leaves_out_chats_open_elsewhere(calc_repo, monkeypatch, phil_home):
    import os

    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    _listed_chat(paths, 1)
    held = _listed_chat(paths, 2)
    (paths.project_dir / "chats" / held / "chat.lock").write_text(str(os.getppid()), encoding="utf-8")
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    [screen] = screens
    texts = [_item_text(item) for item in screen.items]
    assert "1 open chat · /chat" in texts  # the picker wouldn't list the one held elsewhere


def test_full_start_with_no_open_chats_posts_no_hint(calc_repo, monkeypatch, phil_home):
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    [screen] = screens
    assert not any("open chat" in _item_text(item) for item in screen.items)


def test_an_empty_chat_leaves_no_exit_line(calc_repo, monkeypatch, phil_home):
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    assert "To continue this chat" not in result.output


def test_a_chat_left_at_approval_ends_with_the_exit_line(calc_repo, monkeypatch, phil_home):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    classic_repo(calc_repo)
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="add subtract\n", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    [chat_id] = _chat_ids(calc_repo)
    assert result.output.rstrip().endswith(f"To continue this chat:  phil --resume {chat_id}")


def test_screen_inline_uses_the_terminal_and_todays_start(calc_repo, monkeypatch):
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    _listed_chat(paths, 1)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    monkeypatch.setattr(cli, "_screen", _no_terminal)
    made.clear()
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="\n", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    assert "Reopen one?" in _plain(result.output)
    assert len(made) == 1
    assert "To continue this chat" not in result.output


def test_a_small_terminal_falls_back_to_the_inline_chat(calc_repo, monkeypatch, phil_home):
    _full(monkeypatch, phil_home)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    made.clear()
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env={"COLUMNS": "50", "LINES": "30"})
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "This terminal is smaller than 60×15; using the inline chat." in output
    assert output.index("smaller than 60×15") < output.index("Phil v")  # the note comes before the banner
    assert len(made) == 1 and screens == []


def test_phil_chat_starts_in_the_picker_and_switches(calc_repo, monkeypatch, phil_home):
    older, newer = _two_chats(calc_repo, monkeypatch)
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "chat"], input="1\n", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    [screen] = screens
    [first] = _chat_ids(calc_repo) - {older, newer}
    assert [header[2] for header in screen.headers] == [first, newer]
    assert screen.cleared == 1 and screen.discarded == 1
    assert f"Reopened {newer}" in screen.text()
    # The chat it switched to holds a run: the exit line names it.
    assert result.output.rstrip().endswith(f"To continue this chat:  phil --resume {newer}")


def test_phil_chat_with_no_open_chats_starts_a_new_one(calc_repo, monkeypatch, phil_home):
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "chat"], input="", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    [screen] = screens
    assert "No open chats; starting a new one." in screen.text()


def test_an_inline_switch_says_how_to_come_back_to_the_chat_it_leaves(calc_repo, monkeypatch):
    older, newer = _two_chats(calc_repo, monkeypatch)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", older, "chat"], input="1\n")
    assert result.exit_code == 0, result.output
    output = " ".join(_plain(result.output).split())
    assert f"keeps working. Reopen this chat with `phil --resume {older}`." in output
    assert f"Reopened {newer}" in output
    assert output.index(f"phil --resume {older}`") < output.index(f"Reopened {newer}")
    assert "To continue this chat" not in output  # the exit line is the full screen's


def test_continue_reopens_the_most_recent_open_chat(calc_repo, monkeypatch):
    _older, newer = _two_chats(calc_repo, monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--continue"], input="")
    assert result.exit_code == 0, result.output
    assert f"Reopened {newer}" in result.output


def test_continue_with_no_open_chat_starts_a_new_one(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--continue"], input="")
    assert result.exit_code == 0, result.output
    assert "No open chat to continue; starting a new one." in result.output
    assert len(_chat_ids(calc_repo)) == 1


def test_continue_skips_a_chat_open_elsewhere(calc_repo, monkeypatch):
    import os

    older, newer = _two_chats(calc_repo, monkeypatch)
    lock = ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats" / newer / "chat.lock"
    lock.write_text(str(os.getppid()), encoding="utf-8")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--continue"], input="")
    assert result.exit_code == 0, result.output
    assert f"Reopened {older}" in result.output


@pytest.mark.parametrize("other", [["--new"], ["--resume", "c-20260101-000000"]])
def test_continue_with_new_or_resume_is_an_error(calc_repo, other):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--continue", *other], input="")
    assert result.exit_code == 1
    assert "not both" in result.output


def test_a_switch_to_a_chat_held_elsewhere_stays_on_the_current_chat(calc_repo, monkeypatch, phil_home):
    from phil.chat.session import ChatLocked, ChatSession

    older, newer = _two_chats(calc_repo, monkeypatch)
    original = ChatSession.lock

    def lock(self):
        if self.id == newer:  # another window took it after the picker listed it
            raise ChatLocked(4242)
        original(self)

    monkeypatch.setattr(ChatSession, "lock", lock)
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "chat"], input="1\n", env=FULL_ENV)
    assert result.exit_code == 0, result.output
    [screen] = screens
    [first] = _chat_ids(calc_repo) - {older, newer}
    text = " ".join(screen.text().split())
    assert f"Chat {newer} is already open in another window (pid 4242)." in text
    assert f"Staying in chat {first}." in text
    assert [header[2] for header in screen.headers] == [first]
    assert screen.cleared == 0
    lock_file = ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats" / first / "chat.lock"
    assert not lock_file.exists()  # released when the chat ended


def test_a_refused_switch_whose_chat_cant_be_relocked_says_so_after_the_frame(calc_repo, monkeypatch, phil_home):
    from phil.chat.session import ChatLocked, ChatSession

    older, newer = _two_chats(calc_repo, monkeypatch)
    original = ChatSession.lock
    locks: dict[str, int] = {}

    def lock(self):
        locks[self.id] = locks.get(self.id, 0) + 1
        if self.id == newer or locks[self.id] > 1:  # the switch is refused, then the relock too
            raise ChatLocked(5151)
        original(self)

    monkeypatch.setattr(ChatSession, "lock", lock)
    _full(monkeypatch, phil_home)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "chat"], input="1\n", env=FULL_ENV)
    assert result.exit_code == 1
    [first] = _chat_ids(calc_repo) - {older, newer}
    # Printed on the restored terminal (`result.output`), not into the closed frame.
    assert f"Chat {first} is already open in another window (pid 5151)." in " ".join(_plain(result.output).split())


def test_a_crash_inside_the_frame_reaches_the_crash_handler(calc_repo, monkeypatch, phil_home):
    from phil import diagnostics
    from phil.chat.controller import ChatController
    from phil.cli import entry

    def boom(self):
        raise RuntimeError("frame boom")

    monkeypatch.setattr(ChatController, "run", boom)
    _full(monkeypatch, phil_home)
    monkeypatch.setattr(sys, "argv", ["phil", "--repo", str(calc_repo)])
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setenv("LINES", "30")
    try:
        with pytest.raises(SystemExit) as exit_info:
            entry.run(lambda: cli.app)
    finally:
        diagnostics.reset()
    assert exit_info.value.code == 1
    [crash] = sorted((phil_home / "logs").glob("crash-*.log"))
    assert "RuntimeError: frame boom" in crash.read_text(encoding="utf-8")
    [chat_id] = _chat_ids(calc_repo)
    lock_file = ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats" / chat_id / "chat.lock"
    assert not lock_file.exists()
