import os
import subprocess
import sys
import time

from typer.testing import CliRunner

from phil import platform
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
    try:
        deadline = time.monotonic() + 60
        while not is_worker_alive(get_run(conn, record.run_id)) and time.monotonic() < deadline:
            time.sleep(0.2)
        result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
        assert result.exit_code == 0, result.output
        assert proc.wait(timeout=30) == 0
        stopped = get_run(conn, record.run_id)
        assert (stopped.state, stopped.pid) == ("stopped", None)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


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


def test_stop_falls_through_when_worker_already_exited(calc_repo, monkeypatch):
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running")
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    update_run(conn, record.run_id, pid=proc.pid)
    monkeypatch.setattr(cli, "is_worker_alive", lambda record: True)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert result.exit_code == 0, result.output
    stopped = get_run(conn, record.run_id)
    assert stopped.state == "stopped"
    assert stopped.needs_attention == "stopped by user (worker was not running)"


def test_stop_on_windows_falls_through_when_the_worker_exits_without_stopping(calc_repo, monkeypatch):
    # On Windows the request is only a file, so nothing fails when the worker is already gone;
    # phil stop must notice the dead pid rather than wait out the timeout and report a forced stop.
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running")
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    update_run(conn, record.run_id, pid=proc.pid)
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "is_worker_alive", lambda record: True)
    started = time.monotonic()
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id, "--timeout", "30"])
    assert result.exit_code == 0, result.output
    assert time.monotonic() - started < 10
    assert ProjectPaths(info.slug).stop_request(record.run_id).exists()
    stopped = get_run(conn, record.run_id)
    assert (stopped.state, stopped.needs_attention) == ("stopped", "stopped by user (worker was not running)")


def test_stop_reports_when_the_row_ends_in_another_state(calc_repo, monkeypatch):
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running", pid=424242)

    def fake_request_stop(pid, stop_file):
        # The worker, asked to stop (by SIGTERM or by stop file), fails instead.
        update_run(conn, record.run_id, state="failed", needs_attention="boom")

    monkeypatch.setattr(cli, "is_worker_alive", lambda record: True)
    monkeypatch.setattr(platform, "request_stop", fake_request_stop)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id, "--timeout", "2"])
    assert result.exit_code == 1
    assert "ended as failed" in result.output


def test_stop_asks_first_then_kills_a_worker_that_does_not_stop(calc_repo, monkeypatch):
    info, record, conn = new_run(calc_repo)
    stubborn = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        update_run(conn, record.run_id, state="running", pid=stubborn.pid)
        requests, kills = [], []
        real_kill_tree = platform.kill_tree
        monkeypatch.setattr(cli, "is_worker_alive", lambda record: True)
        monkeypatch.setattr(platform, "request_stop", lambda pid, stop_file: requests.append((pid, stop_file)))

        def recording_kill_tree(pid):
            kills.append(pid)
            real_kill_tree(pid)

        monkeypatch.setattr(platform, "kill_tree", recording_kill_tree)
        result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id, "--timeout", "0.5"])
        assert result.exit_code == 0, result.output
        assert requests == [(stubborn.pid, ProjectPaths(info.slug).stop_request(record.run_id))]
        assert kills == [stubborn.pid]
        assert stubborn.wait(timeout=10) != 0
        assert "by force" in result.output
        assert "did not stop in time" in result.output
        assert f"Stopped {record.run_id}" in result.output
        stopped = get_run(conn, record.run_id)
        assert (stopped.state, stopped.needs_attention) == ("stopped", "stopped by user (forced)")
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait()


def test_stop_reports_a_worker_it_could_not_kill_and_leaves_the_row(calc_repo, monkeypatch):
    _, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running", pid=os.getpid())
    monkeypatch.setattr(cli, "is_worker_alive", lambda record: True)
    monkeypatch.setattr(cli, "FORCE_STOP_WAIT_S", 0.2)
    monkeypatch.setattr(platform, "request_stop", lambda pid, stop_file: None)
    monkeypatch.setattr(platform, "kill_tree", lambda pid: None)  # the kill doesn't take
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id, "--timeout", "0.2"])
    assert result.exit_code == 1
    assert f"could not stop the worker (pid {os.getpid()})" in result.output
    assert get_run(conn, record.run_id).state == "running"


def test_stop_on_windows_writes_the_stop_file_instead_of_signalling(calc_repo, monkeypatch):
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running", pid=424242)
    stop_file = ProjectPaths(info.slug).stop_request(record.run_id)

    def no_os_kill(*args):
        raise AssertionError("phil stop must not signal on Windows")

    def worker_sees_the_file(*args):
        # Stands in for the worker: it stops once the stop file appears.
        if stop_file.exists():
            update_run(conn, record.run_id, state="stopped", needs_attention="stopped by user")
        return get_run(conn, record.run_id)

    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(cli.os, "kill", no_os_kill)
    monkeypatch.setattr(cli, "is_worker_alive", lambda record: True)
    monkeypatch.setattr(cli, "get_run", worker_sees_the_file)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id, "--timeout", "5"])
    assert result.exit_code == 0, result.output
    assert stop_file.exists()
    assert "by force" not in result.output
    assert f"Stopped {record.run_id}" in result.output


def test_stop_young_pending_run_is_still_starting(calc_repo):
    info, record, conn = new_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert result.exit_code == 1
    assert "still starting" in result.output
    assert get_run(conn, record.run_id).state == "pending"


def test_stop_stale_pending_run_marks_stopped(calc_repo):
    info, record, conn = new_run(calc_repo)
    conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", ("2000-01-01T00:00:00+00:00", record.run_id))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert result.exit_code == 0, result.output
    assert get_run(conn, record.run_id).state == "stopped"
