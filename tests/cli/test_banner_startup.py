import importlib.metadata
from pathlib import Path

from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, update_run
from phil.ui.banner import FIRST_TIPS, TIPS
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
    assert TIPS in output
    assert "Commands: /runs" not in output


def test_piped_start_prints_plain_facts(calc_repo):
    _add_run(calc_repo, "r-0001", "running", "completed")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "calc @ main" in result.output
    assert TIPS in result.output
    assert not any(ch in result.output for ch in ("╭", "│", "\x1b"))


def test_a_paused_run_shows_in_pick_up(calc_repo):
    paths = _add_run(calc_repo, "r-0002", "running", "escalated")
    run_events(paths, "r-0002").append("escalation", escalation={"summary": "CALC-002 needs approval", "options": []})
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "⏸ r-0002 is waiting for you (CALC-002 needs approval) · /answer" in result.output


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
    assert TIPS not in result.output


def test_version_falls_back_to_dev(calc_repo, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "Phil vdev" in result.output
