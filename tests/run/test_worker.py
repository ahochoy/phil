import os
import signal
import sqlite3
import time

import pytest

import phil.run.worker as worker_module
from phil.agents.fake import ScriptedAgentFactory
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import Heartbeat, StopRequested, WorkerError, run_worker
from phil.store.db import connect
from phil.store.events import EventLog, run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from tests.helpers import run_git
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red


def happy():
    return ScriptedAgentFactory(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )


def new_run(repo):
    info = resolve_repo(repo)
    return info, prepare_run(info, calc_plan(), info.head_sha)


def row(info, run_id):
    conn = connect(ProjectPaths(info.slug).db_path)
    try:
        return get_run(conn, run_id)
    finally:
        conn.close()


def test_prepare_run_writes_the_plan_and_a_pending_row(calc_repo):
    info, record = new_run(calc_repo)
    assert record.state == "pending"
    assert (ProjectPaths(info.slug).run_dir(record.run_id) / "plan.json").exists()


def test_start_runs_to_completion_and_cleans_up(calc_repo):
    info, record = new_run(calc_repo)
    outcome = run_worker(calc_repo, record.run_id, "start", factory=happy(), heartbeat_s=0.05)
    assert outcome.status == "completed"
    final = row(info, record.run_id)
    assert (final.state, final.pid) == ("completed", None)
    assert final.heartbeat_at is not None
    kinds = [e["kind"] for e in run_events(ProjectPaths(info.slug), record.run_id).read()[0]]
    assert kinds[0] == "worker" and kinds[-1] == "outcome"
    assert run_git(calc_repo, "log", "--format=%s", record.branch).splitlines()[0] == "CALC-001: Add subtract"


def test_escalate_then_resume(calc_repo):
    info, record = new_run(calc_repo)
    escalating = ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]})
    assert run_worker(calc_repo, record.run_id, "start", factory=escalating).status == "escalated"
    assert row(info, record.run_id).state == "escalated"
    finishing = ScriptedAgentFactory(
        {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    assert run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing).status == "completed"


def test_mode_guards_do_not_touch_the_row(calc_repo):
    info, record = new_run(calc_repo)
    with pytest.raises(WorkerError, match="not waiting for a decision"):
        run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=happy())
    assert row(info, record.run_id).state == "pending"
    with pytest.raises(WorkerError, match="unknown run"):
        run_worker(calc_repo, "r-ffff", "start", factory=happy())
    with pytest.raises(ValueError):
        run_worker(calc_repo, record.run_id, "sideways", factory=happy())


def test_finished_runs_are_rejected(calc_repo):
    info, record = new_run(calc_repo)
    run_worker(calc_repo, record.run_id, "start", factory=happy())
    with pytest.raises(WorkerError, match="finished"):
        run_worker(calc_repo, record.run_id, "continue", factory=happy())


def test_crash_marks_failed_and_continue_recovers(calc_repo):
    info, record = new_run(calc_repo)
    crashing = ScriptedAgentFactory({"implementer": [write_red, RuntimeError("model went away")]})
    with pytest.raises(RuntimeError):
        run_worker(calc_repo, record.run_id, "start", factory=crashing)
    failed = row(info, record.run_id)
    assert failed.state == "failed"
    assert failed.needs_attention == "worker failed: RuntimeError: model went away"
    recovering = ScriptedAgentFactory(
        {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    assert run_worker(calc_repo, record.run_id, "continue", factory=recovering).status == "completed"


def test_stop_marks_stopped_and_continue_recovers(calc_repo):
    info, record = new_run(calc_repo)

    def interrupted(turn):
        raise StopRequested()

    stopping = ScriptedAgentFactory({"implementer": [write_red, interrupted]})
    assert run_worker(calc_repo, record.run_id, "start", factory=stopping).status == "stopped"
    stopped = row(info, record.run_id)
    assert (stopped.state, stopped.needs_attention, stopped.pid) == ("stopped", "stopped by user", None)
    recovering = ScriptedAgentFactory(
        {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    assert run_worker(calc_repo, record.run_id, "continue", factory=recovering).status == "completed"


def test_heartbeat_updates_the_row(calc_repo):
    info, record = new_run(calc_repo)
    db_path = ProjectPaths(info.slug).db_path
    beat = Heartbeat(db_path, record.run_id, 0.02)
    beat.start()
    time.sleep(0.2)
    beat.stop()
    assert row(info, record.run_id).heartbeat_at is not None


def test_continue_while_a_decision_is_pending_is_rejected(calc_repo):
    info, record = new_run(calc_repo)
    escalating = ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]})
    assert run_worker(calc_repo, record.run_id, "start", factory=escalating).status == "escalated"
    with pytest.raises(WorkerError, match="resume it with an action"):
        run_worker(calc_repo, record.run_id, "continue", factory=happy())
    assert row(info, record.run_id).state == "escalated"


def test_start_on_a_paused_thread_is_rejected(calc_repo):
    info, record = new_run(calc_repo)
    escalating = ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]})
    assert run_worker(calc_repo, record.run_id, "start", factory=escalating).status == "escalated"
    with pytest.raises(WorkerError, match="resume it with an action"):
        run_worker(calc_repo, record.run_id, "start", factory=happy())
    assert row(info, record.run_id).state == "escalated"


def test_continue_on_a_never_started_thread_starts_fresh(calc_repo):
    info, record = new_run(calc_repo)
    assert run_worker(calc_repo, record.run_id, "continue", factory=happy()).status == "completed"


def test_exception_during_outcome_write_does_not_mask_original_or_corrupt_state(calc_repo, monkeypatch):
    info, record = new_run(calc_repo)
    original_append = EventLog.append

    def flaky_append(self, kind, **data):
        if kind == "outcome":
            raise RuntimeError("disk full")
        return original_append(self, kind, **data)

    monkeypatch.setattr(EventLog, "append", flaky_append)
    with pytest.raises(RuntimeError, match="disk full"):
        run_worker(calc_repo, record.run_id, "start", factory=happy())
    assert row(info, record.run_id).state == "completed"


def test_real_sigterm_stops_cleanly_and_restores_handler(calc_repo):
    info, record = new_run(calc_repo)
    original_handler = signal.getsignal(signal.SIGTERM)

    def send_sigterm(turn):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        return write_red(turn)

    stopping = ScriptedAgentFactory({"implementer": [send_sigterm]})
    outcome = run_worker(calc_repo, record.run_id, "start", factory=stopping)
    assert outcome.status == "stopped"
    stopped = row(info, record.run_id)
    assert (stopped.state, stopped.pid) == ("stopped", None)
    assert signal.getsignal(signal.SIGTERM) == original_handler


def test_heartbeat_survives_a_transient_update_failure(calc_repo, monkeypatch):
    info, record = new_run(calc_repo)
    db_path = ProjectPaths(info.slug).db_path
    original_update_run = worker_module.update_run
    calls = {"n": 0}

    def flaky_update_run(conn, run_id, **fields):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_update_run(conn, run_id, **fields)

    monkeypatch.setattr(worker_module, "update_run", flaky_update_run)
    beat = Heartbeat(db_path, record.run_id, 0.02)
    beat.start()
    time.sleep(0.2)
    beat.stop()
    assert calls["n"] >= 2
    assert row(info, record.run_id).heartbeat_at is not None
