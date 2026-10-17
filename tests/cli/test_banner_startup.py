import importlib.metadata
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from phil.chat.session import ChatSession
from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, update_run
from phil.ui.banner import FIRST_TIPS
from tests.cli.test_chat_command import _FakeTerminal, _plain

runner = CliRunner()


def _add_run(repo: Path, run_id: str, *states: str) -> ProjectPaths:
    """A run for `repo`, walked through `states` (from pending)."""
    paths = ProjectPaths(resolve_repo(repo).slug)
    conn = connect(paths.db_path)
    create_run(conn, run_id=run_id, keyword="calc", base_sha="0" * 40, worktree=paths.project_dir / "wt", tasks_total=1)
    for state in states:
        update_run(conn, run_id, state=state)
    return paths


def _tty(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal", _FakeTerminal)


def test_chat_opens_with_the_card_and_no_help_paragraph(calc_repo, monkeypatch):
    _add_run(calc_repo, "r-0001", "running", "completed")  # not a first visit, and nothing to pick up
    _tty(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    box = [line for line in output.splitlines() if line.startswith("│")]
    assert any("Phil v" in line for line in box)
    assert any("calc" in line for line in box)
    assert "Type a goal to start" not in output
    assert "Commands: /runs" not in output


def test_piped_start_prints_plain_facts(calc_repo):
    _add_run(calc_repo, "r-0001", "running", "completed")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "calc @ main" in result.output
    assert "Type a goal to start" not in result.output
    assert not any(ch in result.output for ch in ("╭", "│", "\x1b"))


def test_a_paused_run_shows_in_pick_up(calc_repo):
    paths = _add_run(calc_repo, "r-0002", "running", "escalated")
    run_events(paths, "r-0002").append("escalation", escalation={"summary": "CALC-002 needs approval", "options": []})
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "⏸ r-0002 is waiting for you (CALC-002 needs approval) · phil attach r-0002" in result.output


def test_a_running_run_with_a_dead_worker_shows_as_stopped(calc_repo):
    _add_run(calc_repo, "r-0004", "running")  # no worker pid: the worker is gone
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "r-0004 was stopped · phil resume r-0004" in result.output
    assert "is running" not in result.output


def test_a_running_run_with_a_live_worker_shows_as_running(calc_repo, monkeypatch):
    _add_run(calc_repo, "r-0005", "running")
    monkeypatch.setattr(cli, "is_worker_alive", lambda record: True)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "r-0005 is running · phil attach r-0005" in result.output


def test_a_failing_render_falls_back_to_plain_lines(calc_repo, monkeypatch):
    import phil.ui.banner as banner

    _add_run(calc_repo, "r-0001", "running", "completed")
    _tty(monkeypatch)

    def broken(facts, width, mascot=None):
        raise ValueError("too many values to unpack")

    monkeypatch.setattr(banner, "render_banner", broken)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "calc @ main" in output and "Type a goal to start" not in output
    assert not any(line.startswith("│") for line in output.splitlines())
    assert "you › " in output


def test_the_models_row_shows_high_and_low(calc_repo):
    _add_run(calc_repo, "r-0001", "running", "completed")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    [models] = [line for line in result.output.splitlines() if line.startswith("high ")]
    assert " · low " in models


def _open_chat(paths: ProjectPaths, second: int) -> str:
    chat = ChatSession.create(paths, now=lambda: datetime(2026, 10, 8, 12, 0, second))
    chat.save_state({"run_id": f"r-90{second:02d}", "done_seen": False})
    return chat.id


def test_resume_leaves_the_reopened_chat_out_of_the_reminder(calc_repo):
    paths = _add_run(calc_repo, "r-0001", "running", "completed")
    older, newer = _open_chat(paths, 1), _open_chat(paths, 2)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--resume", newer], input="")
    assert result.exit_code == 0, result.output
    assert f"1 other open chat · phil --resume {older}" in result.output
    assert f"phil --resume {newer}" not in result.output


def test_base_shows_the_ref_and_no_uncommitted(calc_repo):
    _add_run(calc_repo, "r-0001", "running", "completed")
    (calc_repo / "scratch.txt").write_text("dirty\n", encoding="utf-8", newline="\n")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--base", "main"], input="")
    assert result.exit_code == 0, result.output
    assert "calc · base main " in result.output
    assert "uncommitted" not in result.output


def test_a_saved_chat_with_no_runs_is_not_a_first_visit(calc_repo):
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    ChatSession.create(paths)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "Type a goal to start" not in result.output
    assert FIRST_TIPS not in result.output


def test_a_broken_database_leaves_pick_up_out_and_the_chat_starts(calc_repo, monkeypatch):
    _add_run(calc_repo, "r-0003", "running", "escalated")

    def broken(conn):
        raise RuntimeError("database is broken")

    monkeypatch.setattr(cli, "list_runs", broken)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "calc @ main" in result.output
    assert "r-0003" not in result.output
    assert "you › " in result.output  # the chat's first prompt was reached


def test_first_time_tips(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert FIRST_TIPS in result.output


def test_the_open_chats_list_is_the_only_mention_of_its_runs(calc_repo, monkeypatch):
    paths = _add_run(calc_repo, "r-9001", "running", "failed")
    _open_chat(paths, 1)  # its run is r-9001
    _tty(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="\n", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "Open chats:" in output
    assert "1. r-9001 · run failed · (no goal)" in output  # an old, unnamed run shows by its ID
    assert "phil resume r-9001" not in output  # no separate run line
    assert "other open chat" not in output


def test_a_run_outside_the_listed_chats_keeps_its_labelled_run_line(calc_repo, monkeypatch):
    paths = _add_run(calc_repo, "r-9001", "running", "completed")
    _open_chat(paths, 1)  # its run is r-9001
    conn = connect(paths.db_path)
    create_run(conn, run_id="r-0007", keyword="calc", base_sha="0" * 40, worktree=paths.project_dir / "wt",
               tasks_total=1, name="nightly-docs")  # e.g. started with `phil run`: no chat holds it
    update_run(conn, "r-0007", state="running")
    update_run(conn, "r-0007", state="failed")
    _tty(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="\n", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    assert "nightly-docs (r-0007) failed · phil resume r-0007" in _plain(result.output)


def test_an_unheld_pick_up_run_shows_behind_a_newer_one_a_chat_holds(calc_repo, monkeypatch):
    paths = _add_run(calc_repo, "r-9001", "running", "failed")  # older; no chat holds it
    conn = connect(paths.db_path)
    create_run(conn, run_id="r-9002", keyword="calc", base_sha="0" * 40, worktree=paths.project_dir / "wt2",
               tasks_total=1)
    update_run(conn, "r-9002", state="running")
    update_run(conn, "r-9002", state="failed")
    _open_chat(paths, 2)  # newer; its run is r-9002
    _tty(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="\n", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "r-9001 failed · phil resume r-9001" in output
    assert "phil resume r-9002" not in output


def test_a_failing_open_chats_lookup_leaves_the_list_out_and_the_chat_starts(calc_repo, monkeypatch):
    import phil.chat.session as session

    _add_run(calc_repo, "r-0001", "running", "completed")
    _tty(monkeypatch)

    def broken(paths, conn):
        raise RuntimeError("chats are unreadable")

    monkeypatch.setattr(session, "list_open_chats", broken)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "Open chats:" not in output and "you › " in output


def test_the_banner_has_no_parked_count(calc_repo):
    _add_run(calc_repo, "r-0001", "running", "completed")
    from phil.contracts import Ref
    from phil.store.parked import park

    park(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path), raised_by="user", note="later",
         why_not_now="test", source=Ref(label="t", path="t"), run_id=None)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert " · 1 parked" not in result.output  # the banner's old form; the status bar keeps its own count


def test_version_falls_back_to_dev(calc_repo, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "Phil vdev" in result.output
