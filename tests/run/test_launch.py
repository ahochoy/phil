import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from phil.repo import resolve_repo
from phil.run.launch import is_worker_alive, prepare_run, spawn_worker, worker_command
from phil.store.db import connect, utcnow
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from tests.run.conftest import calc_plan

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def worker_env(scenario: str) -> dict:
    return os.environ | {
        "PHIL_AGENT_FACTORY": "tests.run.worker_scenarios:factory",
        "PHIL_TEST_SCENARIO": scenario,
        "PYTHONPATH": str(PROJECT_ROOT),
    }


def new_run(repo):
    info = resolve_repo(repo)
    return info, prepare_run(info, calc_plan(), info.head_sha)


def test_worker_command_shape(tmp_path):
    command = worker_command(tmp_path, "r-0001", "resume", {"action": "retry"})
    assert command[:4] == [sys.executable, "-P", "-m", "phil"]
    assert command[4:] == ["--repo", str(tmp_path), "_worker", "r-0001", "--mode", "resume", "--decision", '{"action": "retry"}']


def test_spawn_worker_passes_env_through_unchanged(calc_repo, monkeypatch):
    info, record = new_run(calc_repo)
    real_popen = subprocess.Popen
    captured = []

    def fake_popen(command, **kwargs):
        # spawn_worker calls resolve_repo (which shells out to git) before spawning the
        # worker itself; only intercept the actual worker invocation, let git through.
        if command[0] != sys.executable:
            return real_popen(command, **kwargs)
        captured.append(kwargs)

        class FakeProc:
            def wait(self, timeout=None):
                return 0

        return FakeProc()

    monkeypatch.setattr("phil.run.launch.subprocess.Popen", fake_popen)

    spawn_worker(calc_repo, record.run_id, "start", env=None)
    assert captured[-1]["env"] is None

    explicit_env = {"FOO": "bar"}
    spawn_worker(calc_repo, record.run_id, "start", env=explicit_env)
    assert captured[-1]["env"] == explicit_env
    assert "PYTHONSAFEPATH" not in captured[-1]["env"]


def test_detached_worker_completes_a_run(calc_repo):
    info, record = new_run(calc_repo)
    proc = spawn_worker(calc_repo, record.run_id, "start", env=worker_env("happy"))
    assert proc.wait(timeout=180) == 0
    paths = ProjectPaths(info.slug)
    assert get_run(connect(paths.db_path), record.run_id).state == "completed"
    assert (paths.run_dir(record.run_id) / "logs" / "worker.log").exists()


def test_detached_escalation_then_resume(calc_repo):
    info, record = new_run(calc_repo)
    paths = ProjectPaths(info.slug)
    assert spawn_worker(calc_repo, record.run_id, "start", env=worker_env("escalate")).wait(timeout=180) == 0
    assert get_run(connect(paths.db_path), record.run_id).state == "escalated"
    assert run_events(paths, record.run_id).latest("escalation")["escalation"]["reason"] == "attempts"
    proc = spawn_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, env=worker_env("finish_after_retry"))
    assert proc.wait(timeout=180) == 0
    assert get_run(connect(paths.db_path), record.run_id).state == "completed"


def test_worker_error_exits_2(calc_repo):
    info, record = new_run(calc_repo)
    proc = spawn_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, env=worker_env("happy"))
    assert proc.wait(timeout=60) == 2


def test_is_worker_alive(calc_repo):
    info, record = new_run(calc_repo)
    assert not is_worker_alive(record)
    me = replace(record, pid=os.getpid(), heartbeat_at=utcnow())
    assert is_worker_alive(me)
    assert not is_worker_alive(replace(me, heartbeat_at="2000-01-01T00:00:00+00:00"))
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    assert not is_worker_alive(replace(me, pid=done.pid))
