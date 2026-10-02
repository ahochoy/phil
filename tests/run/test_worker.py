import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time

import pytest

import phil.run.worker as worker_module
from phil.agents.fake import ScriptedAgentFactory
from phil.config import PhilConfig
from phil.repo import resolve_repo
from phil.run.checkpoint import open_checkpointer
from phil.run.launch import prepare_run
from phil.run.worker import Heartbeat, StopRequested, WorkerError, run_worker
from phil.store.db import connect, utcnow
from phil.store.events import EventLog, run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run, update_run
from tests.helpers import MODELS_TOML, run_git
from tests.run.conftest import TEST_CMD, bad_green, calc_plan, review, tester_report, write_green, write_red


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


def test_quick_run_state_carries_depth_and_skips_the_tester(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha, depth="quick")
    # No "tester" entry: a quick run starts with tester_done already set, so pick_task routes
    # straight to review once every task is done, never calling the tester. Its implementer is
    # the light quick_implementer.
    factory = ScriptedAgentFactory({"quick_implementer": [write_red, write_green], "reviewer": [review()]})
    outcome = run_worker(calc_repo, record.run_id, "start", factory=factory)
    assert outcome.status == "completed"
    assert checkpointed(info, record.run_id)["depth"] == "quick"


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


def test_incomplete_runs_are_finished_too(calc_repo):
    from phil.contracts import Issue
    from tests.run.conftest import review, tester_report, write_green, write_red

    info, record = new_run(calc_repo)
    factory = ScriptedAgentFactory({
        "implementer": [write_red, write_green], "tester": [tester_report()],
        "reviewer": [review("approve", [Issue(severity="blocker", note="Diff is empty")])],
    })
    assert run_worker(calc_repo, record.run_id, "start", factory=factory).status == "incomplete"
    with pytest.raises(WorkerError, match="incomplete; the run is finished"):
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


def escalated_run(calc_repo):
    info, record = new_run(calc_repo)
    escalating = ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]})
    assert run_worker(calc_repo, record.run_id, "start", factory=escalating).status == "escalated"
    return info, record


def escalation_count(info, run_id):
    return [e["kind"] for e in run_events(ProjectPaths(info.slug), run_id).read()[0]].count("escalation")


def finishing():
    return ScriptedAgentFactory({"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]})


@pytest.mark.parametrize("mode", ["continue", "start"])
def test_continue_or_start_on_a_paused_thread_resurfaces_the_pause(calc_repo, mode):
    info, record = escalated_run(calc_repo)
    outcome = run_worker(calc_repo, record.run_id, mode, factory=happy())
    assert outcome.status == "escalated"
    assert outcome.escalation["options"] == ["retry", "skip", "abort"]
    paused = row(info, record.run_id)
    assert (paused.state, paused.pid) == ("escalated", None)
    assert paused.needs_attention
    assert run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing()).status == "completed"


def test_a_pause_lost_by_a_failed_escalation_write_is_recovered_by_continue(calc_repo, monkeypatch):
    info, record = new_run(calc_repo)
    original_append = EventLog.append

    def failing_append(self, kind, **data):
        if kind == "escalation":
            raise RuntimeError("disk full")
        return original_append(self, kind, **data)

    monkeypatch.setattr(EventLog, "append", failing_append)
    escalating = ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]})
    with pytest.raises(RuntimeError, match="disk full"):
        run_worker(calc_repo, record.run_id, "start", factory=escalating)
    assert row(info, record.run_id).state == "failed"
    monkeypatch.setattr(EventLog, "append", original_append)
    outcome = run_worker(calc_repo, record.run_id, "continue", factory=happy())
    assert outcome.status == "escalated"
    assert row(info, record.run_id).state == "escalated"
    assert escalation_count(info, record.run_id) == 1
    assert run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing()).status == "completed"


def live_foreign_pid():
    return os.getppid()


def test_a_failed_guard_leaves_another_workers_pid_alone(calc_repo):
    info, record = new_run(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    other = live_foreign_pid()
    update_run(conn, record.run_id, state="running", pid=other, heartbeat_at=utcnow())
    with pytest.raises(WorkerError, match="not waiting for a decision"):
        run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=happy())
    after = row(info, record.run_id)
    assert (after.state, after.pid) == ("running", other)


def test_a_second_worker_does_not_claim_a_run_held_by_a_live_worker(calc_repo):
    info, record = escalated_run(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    other = live_foreign_pid()
    update_run(conn, record.run_id, pid=other, heartbeat_at=utcnow())
    for mode, decision in (("resume", {"action": "retry"}), ("continue", None)):
        with pytest.raises(WorkerError, match="already being run by another worker"):
            run_worker(calc_repo, record.run_id, mode, decision, factory=finishing())
        after = row(info, record.run_id)
        assert (after.state, after.pid) == ("escalated", other)


def test_a_dead_workers_pid_is_taken_over(calc_repo):
    info, record = new_run(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    child = subprocess.Popen(["true"])
    child.wait()
    update_run(conn, record.run_id, state="running", pid=child.pid, heartbeat_at=utcnow())
    assert run_worker(calc_repo, record.run_id, "continue", factory=happy()).status == "completed"
    assert row(info, record.run_id).pid is None


def test_a_removed_worktree_is_detected_before_resuming(calc_repo):
    info, record = escalated_run(calc_repo)
    shutil.rmtree(record.worktree)
    with pytest.raises(WorkerError, match=f"worktree .* is missing; clean the run with `phil clean {record.run_id}`"):
        run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing())
    after = row(info, record.run_id)
    assert (after.state, after.pid) == ("escalated", None)


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


NEW_CMD = f"{TEST_CMD} -x"


def checkpointed(info, run_id) -> dict:
    saver = open_checkpointer(ProjectPaths(info.slug).db_path)
    try:
        return saver.get_tuple({"configurable": {"thread_id": run_id}}).checkpoint["channel_values"]
    finally:
        saver.conn.close()


def events_of(info, run_id, kind):
    return [e for e in run_events(ProjectPaths(info.slug), run_id).read()[0] if e["kind"] == kind]


def rebaseline_logs(info, run_id):
    return sorted((ProjectPaths(info.slug).run_dir(run_id) / "logs").glob("rebaseline*"))


def set_project_test_cmd(repo, cmd):
    (repo / "phil.toml").write_text(MODELS_TOML + f"[project]\ntest_cmd = {json.dumps(cmd)}\n")


def test_resume_picks_up_a_changed_test_command_from_phil_toml(calc_repo):
    info, record = escalated_run(calc_repo)
    set_project_test_cmd(calc_repo, NEW_CMD)
    outcome = run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing())

    assert outcome.status == "completed"
    assert [e["cmd"] for e in events_of(info, record.run_id, "test_cmd_changed")] == [NEW_CMD]
    state = checkpointed(info, record.run_id)
    assert state["test_cmd"] == NEW_CMD
    assert state["rebaseline"] is False
    assert len(rebaseline_logs(info, record.run_id)) == 1


def test_continue_picks_up_a_changed_test_command_from_phil_toml(calc_repo):
    info, record = new_run(calc_repo)
    crashing = ScriptedAgentFactory({"implementer": [write_red, RuntimeError("model went away")]})
    with pytest.raises(RuntimeError):
        run_worker(calc_repo, record.run_id, "start", factory=crashing)
    set_project_test_cmd(calc_repo, NEW_CMD)
    assert run_worker(calc_repo, record.run_id, "continue", factory=finishing()).status == "completed"

    assert [e["cmd"] for e in events_of(info, record.run_id, "test_cmd_changed")] == [NEW_CMD]
    assert checkpointed(info, record.run_id)["test_cmd"] == NEW_CMD
    assert len(rebaseline_logs(info, record.run_id)) == 1


def test_an_unchanged_test_command_is_not_switched_on_resume(calc_repo):
    info, record = escalated_run(calc_repo)
    set_project_test_cmd(calc_repo, TEST_CMD)  # the same command the run already uses
    assert run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing()).status == "completed"
    assert events_of(info, record.run_id, "test_cmd_changed") == []
    assert rebaseline_logs(info, record.run_id) == []


def test_a_resume_that_fails_after_the_switch_can_be_resumed_again(calc_repo):
    info, record = escalated_run(calc_repo)
    set_project_test_cmd(calc_repo, NEW_CMD)
    crashing = ScriptedAgentFactory({"implementer": [RuntimeError("model went away")]})
    with pytest.raises(RuntimeError):
        run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=crashing)
    assert run_worker(calc_repo, record.run_id, "continue", factory=finishing()).status == "completed"
    assert checkpointed(info, record.run_id)["test_cmd"] == NEW_CMD
    assert len(rebaseline_logs(info, record.run_id)) == 1


def test_start_detects_the_test_command_when_none_is_set(calc_repo):
    (calc_repo / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    run_git(calc_repo, "add", "pytest.ini")
    run_git(calc_repo, "commit", "-m", "pytest.ini")
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan().model_copy(update={"test_cmd": None}), info.head_sha)
    assert run_worker(calc_repo, record.run_id, "start", factory=happy()).status == "completed"
    assert checkpointed(info, record.run_id)["test_cmd"] == "pytest"


def test_an_approved_plan_command_that_differs_from_an_unchanged_phil_toml_stays(calc_repo):
    set_project_test_cmd(calc_repo, "make check")  # set before launch; the plan's own command was approved
    run_git(calc_repo, "add", "phil.toml")
    run_git(calc_repo, "commit", "-m", "config")
    info, record = escalated_run(calc_repo)
    assert checkpointed(info, record.run_id)["config_test_cmd"] == "make check"
    assert run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing()).status == "completed"
    assert events_of(info, record.run_id, "test_cmd_changed") == []
    assert checkpointed(info, record.run_id)["test_cmd"] == TEST_CMD


def test_a_phil_toml_change_after_launch_switches_even_when_it_had_a_value(calc_repo):
    set_project_test_cmd(calc_repo, "make check")
    run_git(calc_repo, "add", "phil.toml")
    run_git(calc_repo, "commit", "-m", "config")
    info, record = escalated_run(calc_repo)
    set_project_test_cmd(calc_repo, NEW_CMD)
    assert run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing()).status == "completed"
    assert [e["cmd"] for e in events_of(info, record.run_id, "test_cmd_changed")] == [NEW_CMD]
    state = checkpointed(info, record.run_id)
    assert (state["test_cmd"], state["config_test_cmd"]) == (NEW_CMD, NEW_CMD)


def config_with(cmd):
    return PhilConfig.model_validate({"project": {"test_cmd": cmd}} if cmd else {})


def test_a_checkpoint_from_before_config_test_cmd_switches_only_for_a_plan_without_a_command():
    legacy_with_plan_cmd = {"test_cmd": "pytest", "plan": {"test_cmd": "pytest"}}
    assert worker_module._test_cmd_switch(legacy_with_plan_cmd, config_with("make check"), "r-1") is None
    legacy_without = {"test_cmd": "pytest", "plan": {"test_cmd": None}}
    assert worker_module._test_cmd_switch(legacy_without, config_with("make check"), "r-1") == {
        "test_cmd": "make check", "rebaseline": True, "config_test_cmd": "make check",
    }
    assert worker_module._test_cmd_switch(legacy_without, config_with("pytest"), "r-1") is None
    assert worker_module._test_cmd_switch(legacy_without, config_with(None), "r-1") is None


def test_the_recorded_config_decides_the_switch():
    values = {"test_cmd": "pytest -x", "plan": {"test_cmd": "pytest -x"}, "config_test_cmd": "pytest"}
    assert worker_module._test_cmd_switch(values, config_with("pytest"), "r-1") is None
    assert worker_module._test_cmd_switch(values, config_with(None), "r-1") is None
    assert worker_module._test_cmd_switch(values, config_with("make test"), "r-1") == {
        "test_cmd": "make test", "rebaseline": True, "config_test_cmd": "make test",
    }


def test_a_resumed_worker_keeps_the_runs_set_overrides(calc_repo, monkeypatch):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha, overrides=["run.max_cost_usd=5"])
    assert json.loads(row(info, record.run_id).config_overrides) == ["run.max_cost_usd=5"]
    real_load_config = worker_module.load_config
    loaded = []

    def spy(root, *, overrides=()):
        config = real_load_config(root, overrides=overrides)
        loaded.append((list(overrides), config.run.max_cost_usd, config.sources["run.max_cost_usd"]))
        return config

    monkeypatch.setattr(worker_module, "load_config", spy)
    crashing = ScriptedAgentFactory({"implementer": [write_red, RuntimeError("model went away")]})
    with pytest.raises(RuntimeError):
        run_worker(calc_repo, record.run_id, "start", factory=crashing)
    recovering = ScriptedAgentFactory(
        {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    assert run_worker(calc_repo, record.run_id, "continue", factory=recovering).status == "completed"
    assert loaded == [(["run.max_cost_usd=5"], 5.0, "--set")] * 2


def test_a_run_without_overrides_stores_none(calc_repo):
    info, record = new_run(calc_repo)
    assert record.config_overrides is None
