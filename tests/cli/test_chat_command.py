from typer.testing import CliRunner

from phil.cli import main as cli
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


def test_chat_requires_chat_models(calc_repo):
    (calc_repo / "phil.toml").write_text('[models]\nimplementer = "test:model"\n')
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 1
    assert "orchestrator, architect, critic" in result.output


def test_chat_warns_about_uncommitted_files(calc_repo, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.chat.chat_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "approve")
    (calc_repo / "scratch.txt").write_text("wip")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0
    assert "1 uncommitted file" in result.output


def test_chat_with_a_bad_base(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "--base", "no-such-ref"], input="")
    assert result.exit_code == 1
