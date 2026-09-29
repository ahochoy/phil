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


def test_thread_start_and_stop(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, interval_s=0.01)
    watcher.start()
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="completed")
    watcher._thread.join(timeout=5)  # the thread ends by itself after run_done
    assert watcher.done
    watcher.stop()
