from typer.testing import CliRunner

from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.checkpoint import open_checkpointer
from phil.run.launch import prepare_run
from phil.run.runner import thread_config
from phil.run.worker import run_worker
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from tests.helpers import run_git
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red

runner = CliRunner()


def finished_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    factory = ScriptedAgentFactory({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]})
    run_worker(calc_repo, record.run_id, "start", factory=factory)
    return info, record, ProjectPaths(info.slug)


def test_diff_shows_the_run_changes(calc_repo):
    info, record, paths = finished_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "diff", record.run_id])
    assert result.exit_code == 0
    assert "def subtract" in result.output


def test_clean_keeps_only_the_summary(calc_repo):
    info, record, paths = finished_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", record.run_id])
    assert result.exit_code == 0, result.output
    run_dir = paths.run_dir(record.run_id)
    assert [p.name for p in run_dir.iterdir()] == ["summary.md"]
    assert not paths.worktree_dir(record.run_id).exists()
    assert run_git(calc_repo, "branch", "--list", record.branch).strip() == ""
    assert run_git(calc_repo, "for-each-ref", f"refs/phil/{record.run_id}/").strip() == ""
    saver = open_checkpointer(paths.db_path)
    assert saver.get_tuple(thread_config(record.run_id)) is None
    assert get_run(connect(paths.db_path), record.run_id).state == "cleaned"
    assert runner.invoke(cli.app, ["--repo", str(calc_repo), "diff", record.run_id]).exit_code == 1


def test_clean_purge_removes_the_run_dir(calc_repo):
    info, record, paths = finished_run(calc_repo)
    assert runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", "--purge", record.run_id]).exit_code == 0
    assert not paths.run_dir(record.run_id).exists()


def test_clean_refuses_a_paused_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    run_worker(calc_repo, record.run_id, "start", factory=ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]}))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", record.run_id])
    assert result.exit_code == 1
    assert "escalated" in result.output
