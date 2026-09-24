import json

from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from tests.helpers import run_git
from tests.run.conftest import calc_plan

runner = CliRunner()


def plan_file(tmp_path, **overrides):
    path = tmp_path / "plan.json"
    path.write_text(calc_plan().model_copy(update=overrides).model_dump_json())
    return path


def runs_for(repo):
    return list_runs(connect(ProjectPaths(resolve_repo(repo).slug).db_path))


def test_run_spawns_a_worker(calc_repo, tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(cli, "spawn_worker", lambda repo, run_id, mode, *a, **k: spawned.append((run_id, mode)))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(plan_file(tmp_path))])
    assert result.exit_code == 0, result.output
    [record] = runs_for(calc_repo)
    assert spawned == [(record.run_id, "start")]
    assert f"phil attach {record.run_id}" in result.output
    assert record.base_sha == run_git(calc_repo, "rev-parse", "HEAD").strip()


def test_run_requires_a_test_command(calc_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(plan_file(tmp_path, test_cmd=None))])
    assert result.exit_code == 1
    assert "no test_cmd" in result.output
    assert runs_for(calc_repo) == []


def test_run_rejects_an_invalid_plan(calc_repo, tmp_path):
    bad = tmp_path / "plan.json"
    bad.write_text(json.dumps({"keyword": "calc"}))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(bad)])
    assert result.exit_code == 1
    assert "invalid plan" in result.output


def test_run_warns_about_uncommitted_files(calc_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    (calc_repo / "scratch.txt").write_text("wip")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(plan_file(tmp_path))])
    assert "1 uncommitted file" in result.output


def test_run_in_the_foreground(calc_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.run.worker_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "happy")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", "--foreground", str(plan_file(tmp_path))])
    assert result.exit_code == 0, result.output
    [record] = runs_for(calc_repo)
    assert record.state == "completed"
    assert "completed" in result.output
