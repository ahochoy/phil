import logging

from phil.chat.watcher import RunWatcher
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
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
