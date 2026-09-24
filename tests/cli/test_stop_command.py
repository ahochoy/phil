import time

from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.launch import is_worker_alive, prepare_run, spawn_worker
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run, update_run
from tests.run.conftest import calc_plan
from tests.run.test_launch import worker_env

runner = CliRunner()


def new_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    return info, record, connect(ProjectPaths(info.slug).db_path)


def test_stop_a_live_worker(calc_repo):
    info, record, conn = new_run(calc_repo)
    proc = spawn_worker(calc_repo, record.run_id, "start", env=worker_env("slow"))
    deadline = time.monotonic() + 60
    while not is_worker_alive(get_run(conn, record.run_id)) and time.monotonic() < deadline:
        time.sleep(0.2)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert result.exit_code == 0, result.output
    assert proc.wait(timeout=30) == 0
    stopped = get_run(conn, record.run_id)
    assert (stopped.state, stopped.pid) == ("stopped", None)


def test_stop_without_a_worker_marks_stopped(calc_repo):
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert result.exit_code == 0
    assert get_run(conn, record.run_id).needs_attention == "stopped by user (worker was not running)"


def test_stop_refuses_paused_and_finished_runs(calc_repo):
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running")
    update_run(conn, record.run_id, state="escalated")
    paused = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert paused.exit_code == 1
    assert "--action abort" in paused.output
    update_run(conn, record.run_id, state="aborted")
    assert runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id]).exit_code == 1
