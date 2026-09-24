import time

import pytest

from phil.agents.fake import ScriptedAgentFactory
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import Heartbeat, StopRequested, WorkerError, run_worker
from phil.store.db import connect
from phil.store.events import run_events
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
    return get_run(connect(ProjectPaths(info.slug).db_path), run_id)


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
