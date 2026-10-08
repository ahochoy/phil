import json
import logging

from phil.chat.watcher import RunWatcher
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.store.activity import activity_log
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import update_run
from tests.run.conftest import calc_plan


def setup(calc_repo, **kw):
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    record = prepare_run(info, calc_plan(), info.head_sha)
    posted = []
    now = [1000.0]
    kw.setdefault("alive", lambda record: False)
    kw.setdefault("starting", lambda events: False)
    watcher = RunWatcher(paths, record.run_id, posted.append, clock=lambda: now[0], **kw)
    return paths, record.run_id, connect(paths.db_path), run_events(paths, record.run_id), watcher, posted, now


def kinds(posted):
    return [e.kind for e in posted]


def test_progress_then_done(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, alive=lambda r: True)
    update_run(conn, run_id, state="running", current_node="implement")
    watcher.poll_once()
    assert kinds(posted) == ["run_progress"] and posted[0].data["node"] == "implement"
    watcher.poll_once()
    assert kinds(posted) == ["run_progress"]  # nothing changed
    update_run(conn, run_id, state="completed", tasks_done=1)
    watcher.poll_once()
    assert kinds(posted)[-1] == "run_done" and posted[-1].data["state"] == "completed"
    assert watcher.done


def test_an_incomplete_run_ends_the_watch_even_with_a_worker_alive(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, alive=lambda r: True)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="incomplete", tasks_done=1)
    watcher.poll_once()
    assert kinds(posted)[-1] == "run_done" and posted[-1].data["state"] == "incomplete"
    assert watcher.done


def test_pause_is_posted_once_and_resume_detected(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="escalated", needs_attention="CALC-001 failed 3 attempts")
    events.append("escalation", escalation={"summary": "CALC-001 failed 3 attempts", "options": ["retry", "skip", "abort"]})
    watcher.poll_once()
    watcher.poll_once()
    assert kinds(posted).count("run_paused") == 1
    assert posted[-1].data["escalation"]["options"] == ["retry", "skip", "abort"]
    update_run(conn, run_id, state="running")
    watcher.poll_once()
    assert "run_resumed" in kinds(posted)


def test_no_pause_while_a_worker_is_alive(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, alive=lambda r: True)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="escalated")
    events.append("escalation", escalation={"summary": "x", "options": ["abort"]})
    watcher.poll_once()
    assert "run_paused" not in kinds(posted)


def test_worker_lost_after_a_grace_period(calc_repo):
    paths, run_id, conn, events, watcher, posted, now = setup(calc_repo, lost_after_s=30.0)
    update_run(conn, run_id, state="running")
    watcher.poll_once()
    assert "worker_lost" not in kinds(posted)
    now[0] += 31
    watcher.poll_once()
    watcher.poll_once()
    assert kinds(posted).count("worker_lost") == 1


def test_failed_run_ends_the_watch(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="failed", needs_attention="worker failed: boom")
    watcher.poll_once()
    assert posted[-1].kind == "run_done" and posted[-1].data["needs_attention"] == "worker failed: boom"


def test_failed_run_stays_open_while_a_resume_worker_is_starting(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, starting=lambda e: True)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="failed", needs_attention="worker failed: boom")
    watcher.poll_once()
    assert "run_done" not in kinds(posted)
    assert not watcher.done
    watcher.starting = lambda e: False
    watcher.poll_once()
    assert kinds(posted)[-1] == "run_done"
    assert watcher.done


def test_watch_error_posted_after_five_consecutive_failures(calc_repo, monkeypatch, caplog):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(watcher, "poll_once", boom)
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            watcher._tick()
    assert kinds(posted).count("watch_error") == 1
    assert posted[-1].data["error"] == "RuntimeError: boom"
    assert any(record.exc_info for record in caplog.records)

    watcher._tick()
    assert kinds(posted).count("watch_error") == 1  # still just once until a success

    monkeypatch.setattr(watcher, "poll_once", lambda: None)
    watcher._tick()  # a success resets the streak and re-arms the report

    monkeypatch.setattr(watcher, "poll_once", boom)
    for _ in range(5):
        watcher._tick()
    assert kinds(posted).count("watch_error") == 2


def test_budget_warning_posted_once_per_event(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    events.append(
        "budget_warning", tokens=600, cost_usd=0.0, max_tokens=700, max_cost_usd=2.0, cost_source="reported"
    )
    watcher.poll_once()
    watcher.poll_once()
    assert kinds(posted).count("budget_warning") == 1
    warning = [e for e in posted if e.kind == "budget_warning"][0]
    assert warning.data == {
        "tokens": 600, "cost_usd": 0.0, "max_tokens": 700, "max_cost_usd": 2.0, "cost_source": "reported",
    }
    events.append(
        "budget_warning", tokens=650, cost_usd=0.0, max_tokens=700, max_cost_usd=2.0, cost_source="reported"
    )
    watcher.poll_once()
    assert kinds(posted).count("budget_warning") == 2


def test_run_progress_carries_tokens_cost_and_cost_source(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, alive=lambda r: True)
    update_run(conn, run_id, state="running", current_node="implement")
    watcher.poll_once()
    progress = [e for e in posted if e.kind == "run_progress"][0]
    assert (progress.data["tokens"], progress.data["cost_usd"], progress.data["cost_source"]) == (0, 0.0, "reported")


def test_thread_start_and_stop(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, interval_s=0.01)
    watcher.start()
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="completed")
    watcher._thread.join(timeout=5)  # the thread ends by itself after run_done
    assert watcher.done
    watcher.stop()


def test_rearm_reposts_a_pause_still_waiting_after_a_resume_attempt(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="escalated")
    events.append("escalation", escalation={"summary": "x", "options": ["abort"]})
    watcher.poll_once()
    watcher.poll_once()
    assert kinds(posted).count("run_paused") == 1
    watcher.rearm()  # a resume worker was spawned but exited before claiming the row
    watcher.poll_once()
    assert kinds(posted).count("run_paused") == 2
    watcher.poll_once()
    assert kinds(posted).count("run_paused") == 2


def test_chat_logging_keeps_watcher_tracebacks_off_the_terminal(calc_repo, tmp_path, monkeypatch, capfd, caplog):
    import threading

    from phil.chat.session import chat_logging

    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(watcher, "poll_once", boom)
    capfd.readouterr()
    with caplog.at_level(logging.WARNING), chat_logging(tmp_path / "chat"):
        thread = threading.Thread(target=watcher._tick)
        thread.start()
        thread.join(5)
    assert capfd.readouterr().err == ""
    assert caplog.records == []  # nothing propagates to the root logger's (terminal) handlers
    log = (tmp_path / "chat" / "phil.log").read_text()
    assert "run watcher poll failed" in log and "RuntimeError: boom" in log
    assert logging.getLogger("phil").propagate  # restored when the chat ends


def test_a_budget_warning_from_before_the_watcher_started_is_not_reposted(calc_repo):
    # Reopening or resuming a chat starts a new watcher over the same event log: a warning the
    # user was already shown must not be printed again.
    paths, run_id, conn, events, first, posted, now = setup(calc_repo)
    update_run(conn, run_id, state="running")
    events.append(
        "budget_warning", tokens=600, cost_usd=0.0, max_tokens=700, max_cost_usd=2.0, cost_source="reported"
    )
    reopened = []
    watcher = RunWatcher(paths, run_id, reopened.append, alive=lambda r: False, starting=lambda e: False,
                         clock=lambda: now[0])
    watcher.poll_once()
    assert "budget_warning" not in kinds(reopened)
    events.append(
        "budget_warning", tokens=690, cost_usd=0.0, max_tokens=700, max_cost_usd=2.0, cost_source="reported"
    )
    watcher.poll_once()
    assert [e.data["tokens"] for e in reopened if e.kind == "budget_warning"] == [690]


def test_watcher_posts_budget_raised_and_seeds_it_from_the_log(calc_repo):
    """A budget_raised already in events.jsonl when the watcher starts is posted on its first poll;
    a later one is posted when it appears; the same event is never posted twice."""
    paths, run_id, conn, events, _, _, now = setup(calc_repo)
    update_run(conn, run_id, state="running")
    events.append("budget_raised", max_cost_usd=2.4, max_tokens=1600)
    reopened = []
    watcher = RunWatcher(paths, run_id, reopened.append, alive=lambda r: False, starting=lambda e: False,
                         clock=lambda: now[0])
    watcher.poll_once()
    assert [e.data for e in reopened if e.kind == "budget_raised"] == [{"max_cost_usd": 2.4, "max_tokens": 1600}]
    watcher.poll_once()
    assert kinds(reopened).count("budget_raised") == 1  # not reposted

    events.append("budget_raised", max_cost_usd=4.8, max_tokens=3200)
    watcher.poll_once()
    assert [e.data for e in reopened if e.kind == "budget_raised"] == [
        {"max_cost_usd": 2.4, "max_tokens": 1600}, {"max_cost_usd": 4.8, "max_tokens": 3200},
    ]
    watcher.poll_once()
    assert kinds(reopened).count("budget_raised") == 2  # still just the two


def test_test_cmd_changed_is_posted_once_per_event(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    events.append("test_cmd_changed", cmd="make test")
    watcher.poll_once()
    watcher.poll_once()
    changes = [e for e in posted if e.kind == "test_cmd_changed"]
    assert [e.data for e in changes] == [{"cmd": "make test"}]


def test_a_test_cmd_change_from_before_the_watcher_started_is_not_reposted(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    events.append("test_cmd_changed", cmd="make test")
    reopened_posts = []
    reopened = RunWatcher(paths, run_id, reopened_posts.append, alive=lambda r: True, starting=lambda e: False)
    reopened.poll_once()
    assert "test_cmd_changed" not in kinds(reopened_posts)


# --- the activity feed -----------------------------------------------------------------------------


def _shell(log, command="pytest -q", result="→ 7 passed", *, end=True):
    """Record one run_shell call (its start, and its end unless `end=False`); returns its seq."""
    summary = f"run {command}"
    seq = log.start(task="CALC-001", role="implementer", tool="run_shell", summary=summary)
    if end:
        log.end(seq, task="CALC-001", role="implementer", tool="run_shell", summary=summary, result=result,
                ok=True, detail="exit_code: 0\n7 passed", duration_ms=1200)
    return seq


def _watch(paths, run_id, now):
    posted = []
    watcher = RunWatcher(paths, run_id, posted.append, alive=lambda r: True, starting=lambda e: False,
                         clock=lambda: now[0])
    return watcher, posted


def test_watcher_starts_at_the_end_and_posts_only_new_activity(calc_repo):
    """Given activity.jsonl and events.jsonl with old records before the watcher is created:
    - the first poll posts no 'activity' and no 'milestone' events;
    - after appending one end record and one task_started milestone, the next poll posts exactly one
      'activity' event whose records are the new start+end, and one 'milestone' event."""
    paths, run_id, conn, events, _, _, now = setup(calc_repo)
    update_run(conn, run_id, state="running")
    log = activity_log(paths, run_id)
    _shell(log, "pytest -q tests/old")
    events.append("task_started", task="CALC-000", title="Old", seq=1)
    watcher, posted = _watch(paths, run_id, now)

    watcher.poll_once()
    assert "activity" not in kinds(posted) and "milestone" not in kinds(posted)

    events.append("task_started", task="CALC-001", title="Add subtract", seq=1)
    seq = _shell(log)
    watcher.poll_once()
    activity = [e for e in posted if e.kind == "activity"]
    assert len(activity) == 1
    records = activity[0].data["records"]
    assert [(r["seq"], r["phase"]) for r in records] == [(seq, "start"), (seq, "end")]
    assert records[1]["summary"] == "run pytest -q" and records[1]["result"] == "→ 7 passed"
    milestones = [e for e in posted if e.kind == "milestone"]
    assert len(milestones) == 1
    assert (milestones[0].data["kind"], milestones[0].data["task"]) == ("task_started", "CALC-001")


def test_watcher_seeds_the_live_step_from_a_running_tool(calc_repo):
    """A start record without an end exists before the watcher starts: the first poll posts
    live_step with that record's summary; after its end record is appended, the next poll posts live_step {}."""
    paths, run_id, conn, events, _, _, now = setup(calc_repo)
    update_run(conn, run_id, state="running")
    log = activity_log(paths, run_id)
    seq = _shell(log, end=False)
    watcher, posted = _watch(paths, run_id, now)

    watcher.poll_once()
    live = [e for e in posted if e.kind == "live_step"]
    assert len(live) == 1
    assert (live[0].data["task"], live[0].data["role"], live[0].data["summary"]) == (
        "CALC-001", "implementer", "run pytest -q"
    )
    assert isinstance(live[0].data["started"], float)

    log.end(seq, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
            result="→ 7 passed", ok=True, detail=None, duration_ms=900)
    watcher.poll_once()
    live = [e for e in posted if e.kind == "live_step"]
    assert len(live) == 2 and live[1].data == {}
    watcher.poll_once()
    assert kinds(posted).count("live_step") == 2  # posted only when it changes


def test_milestones_are_posted_between_the_tool_records_they_follow(calc_repo):
    """One poll whose activity holds records seq 1–4 and whose events hold task_done with seq=2:
    activity [1, 2], then task_done, then activity [3, 4]."""
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    log = activity_log(paths, run_id)
    for n in range(1, 5):
        log.record(task="CALC-001", role="implementer", tool="read_file", summary=f"read f{n}.py", result="",
                   ok=True, detail=None, duration_ms=1)
    events.append("task_done", task="CALC-001", files=1, seq=2)
    watcher.poll_once()
    feed = [e for e in posted if e.kind in ("activity", "milestone")]
    assert [e.kind for e in feed] == ["activity", "milestone", "activity"]
    assert sorted({r["seq"] for r in feed[0].data["records"]}) == [1, 2]
    assert feed[1].data["kind"] == "task_done"
    assert sorted({r["seq"] for r in feed[2].data["records"]}) == [3, 4]


def test_a_milestone_without_a_seq_keeps_its_file_position(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    _shell(activity_log(paths, run_id))
    events.append("task_started", task="CALC-001", title="Add subtract")
    watcher.poll_once()
    feed = [e.kind for e in posted if e.kind in ("activity", "milestone")]
    assert feed == ["milestone", "activity"]


def test_ending_an_older_call_with_the_same_summary_keeps_the_newer_live(calc_repo):
    """Two concurrent starts with the same summary: ending the older one doesn't clear the newer one."""
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    log = activity_log(paths, run_id)
    first = _shell(log, end=False)
    second = _shell(log, end=False)
    watcher.poll_once()
    live = [e for e in posted if e.kind == "live_step"]
    assert len(live) == 1 and live[0].data["seq"] == second

    log.end(first, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
            result="→ 7 passed", ok=True, detail=None, duration_ms=900)
    watcher.poll_once()
    assert kinds(posted).count("live_step") == 1  # the newer call is still the live step

    log.end(second, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
            result="→ 7 passed", ok=True, detail=None, duration_ms=900)
    watcher.poll_once()
    live = [e for e in posted if e.kind == "live_step"]
    assert len(live) == 2 and live[1].data == {}


def test_when_the_live_call_ends_the_newest_open_call_becomes_live(calc_repo):
    """Start 1 (an outer sub-agent), start 2, end 2: the live step falls back to seq 1, not empty."""
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    log = activity_log(paths, run_id)
    outer = log.start(task="CALC-001", role="implementer", tool="task", summary="agent explore the repo")
    watcher.poll_once()
    inner = _shell(log, end=False)
    watcher.poll_once()
    live = [e for e in posted if e.kind == "live_step"]
    assert [step.data["seq"] for step in live] == [outer, inner]

    log.end(inner, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
            result="→ 7 passed", ok=True, detail=None, duration_ms=900)
    watcher.poll_once()
    live = [e for e in posted if e.kind == "live_step"]
    assert len(live) == 3
    assert (live[2].data["seq"], live[2].data["summary"]) == (outer, "agent explore the repo")


def test_a_spawn_clears_the_previous_workers_unfinished_calls(calc_repo):
    """An open start (seq 3) from a worker, then a spawn, then the new worker's start seq 4 and its
    end: the live step is empty, not seq 3."""
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    log = activity_log(paths, run_id)
    for _ in range(2):
        _shell(log)
    old = _shell(log, end=False)
    assert old == 3
    watcher.poll_once()
    assert [e.data.get("seq") for e in posted if e.kind == "live_step"] == [3]

    events.append("spawn", pid=12345, mode="resume")
    new = _shell(log)
    assert new == 4
    watcher.poll_once()
    live = [e for e in posted if e.kind == "live_step"]
    assert live[-1].data == {}


def test_a_watcher_for_a_run_without_a_live_worker_does_not_seed_the_live_step(calc_repo):
    paths, run_id, conn, events, _, _, now = setup(calc_repo)
    update_run(conn, run_id, state="running")
    _shell(activity_log(paths, run_id), end=False)  # the dead worker's unfinished call
    posted = []
    watcher = RunWatcher(paths, run_id, posted.append, alive=lambda r: False, starting=lambda e: True,
                         clock=lambda: now[0])
    watcher.poll_once()
    watcher.poll_once()
    assert "live_step" not in kinds(posted)


def test_a_call_from_before_the_latest_spawn_does_not_seed_the_live_step(calc_repo):
    # `/resume` spawns a new worker, then follows the run: the old worker's unfinished call isn't running.
    paths, run_id, conn, events, _, _, now = setup(calc_repo)
    update_run(conn, run_id, state="running")
    log = activity_log(paths, run_id)
    _shell(log, end=False)
    with log.path.open("r", encoding="utf-8") as handle:
        started = json.loads(handle.readline())["ts"]
    events.append("spawn", pid=12345, mode="resume")
    assert events.latest("spawn")["ts"] >= started
    watcher, posted = _watch(paths, run_id, now)  # the new worker is alive
    watcher.poll_once()
    assert "live_step" not in kinds(posted)


def test_a_batch_of_starts_alone_posts_no_activity(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    _shell(activity_log(paths, run_id), end=False)
    watcher.poll_once()
    assert "activity" not in kinds(posted)
    assert [e.data["summary"] for e in posted if e.kind == "live_step"] == ["run pytest -q"]


def test_watcher_skips_a_half_written_activity_line(calc_repo):
    """A trailing partial line is not posted; once completed it is."""
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    path = activity_log(paths, run_id).path
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"seq": 1, "ts": "2026-10-07T10:00:00Z", "phase": "end", "task": "CALC-001",
                       "role": "implementer", "tool": "read_file", "summary": "read calc.py", "result": "",
                       "ok": True, "detail": None, "duration_ms": 3})
    half = len(line) // 2
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line[:half])
    watcher.poll_once()
    assert "activity" not in kinds(posted)

    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line[half:] + "\n")
    watcher.poll_once()
    activity = [e for e in posted if e.kind == "activity"]
    assert len(activity) == 1
    assert [r["summary"] for r in activity[0].data["records"]] == ["read calc.py"]
