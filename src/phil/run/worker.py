import logging
import os
import signal
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from phil.agents.invoke import AgentFactory
from phil.config import load_config
from phil.repo import resolve_repo
from phil.run import runner
from phil.run.checkpoint import open_checkpointer
from phil.run.engine import RunDeps, RunEngine
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect, utcnow
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.run.launch import is_worker_alive
from phil.store.runs import TRANSITIONS, claim_run, get_run, release_run, update_run
from phil.workspace.shell import kill_active_groups

_logger = logging.getLogger(__name__)

HEARTBEAT_S = 5.0
MODES = ("start", "resume", "continue")
FINISHED = ("completed", "aborted", "cleaned")


class StopRequested(BaseException):
    """Raised in the worker's main thread when it receives SIGTERM."""


class WorkerError(Exception):
    pass


def _raise_stop(signum: int, frame: object) -> None:
    raise StopRequested()


def _record_terminal_state(conn: sqlite3.Connection, run_id: str, state: str, needs_attention: str) -> str:
    """Best-effort terminal-state write for an exception handler.

    Only writes `state` if the transition table allows it from the row's current state (e.g. the
    finish node may already have set `completed`/`aborted` before the interrupt/exception landed).
    Returns the row's state after the attempt so the caller can tell whether its write took effect.
    Writes nothing unless this process still holds the row.
    """
    current = get_run(conn, run_id)
    if current is None:
        return state
    if current.pid != os.getpid():
        return current.state
    if state == current.state or state in TRANSITIONS.get(current.state, set()):
        return update_run(conn, run_id, state=state, needs_attention=needs_attention).state
    return current.state


class Heartbeat:
    def __init__(self, db_path: Path, run_id: str, interval_s: float) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, args=(db_path, run_id, interval_s), daemon=True)
        self._started = False

    def _beat(self, db_path: Path, run_id: str, interval_s: float) -> None:
        conn = connect(db_path)
        try:
            while not self._stop.wait(interval_s):
                try:
                    update_run(conn, run_id, heartbeat_at=utcnow())
                except Exception:
                    _logger.warning("heartbeat update failed for run %s", run_id, exc_info=True)
        finally:
            conn.close()

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._started:
            self._thread.join(timeout=5)


def run_worker(
    repo_root: Path,
    run_id: str,
    mode: str,
    decision: dict | None = None,
    *,
    factory: AgentFactory | None = None,
    heartbeat_s: float = HEARTBEAT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> runner.RunOutcome:
    if mode not in MODES:
        raise ValueError(f"unknown worker mode {mode!r}; expected one of {MODES}")
    info = resolve_repo(repo_root)
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    record = get_run(conn, run_id)
    if record is None:
        conn.close()
        raise WorkerError(f"unknown run {run_id}")
    if record.state in FINISHED:
        conn.close()
        raise WorkerError(f"{run_id} is {record.state}; the run is finished")
    config = load_config(info.root)
    events = run_events(paths, run_id)
    deps = RunDeps(
        config=config,
        conn=conn,
        repo_root=info.root,
        run_id=run_id,
        worktree=Path(record.worktree),
        artifacts=ArtifactStore(paths.run_dir(run_id)),
        factory=factory,
        sleep=sleep,
        events=events,
    )
    engine = RunEngine(deps)
    saver = open_checkpointer(paths.db_path)
    graph = engine.build(saver)
    heartbeat = Heartbeat(paths.db_path, run_id, heartbeat_s)
    installed = threading.current_thread() is threading.main_thread()
    previous_handler = signal.signal(signal.SIGTERM, _raise_stop) if installed else None
    claimed = False
    try:
        snapshot = graph.get_state(runner.thread_config(run_id))
        if mode == "resume" and not snapshot.interrupts:
            raise WorkerError(f"{run_id} is not waiting for a decision")
        if snapshot.values and not Path(record.worktree).exists():
            raise WorkerError(
                f"{run_id}'s worktree {record.worktree} is missing; clean the run with `phil clean {run_id}`"
            )
        stale_pid = record.pid if record.pid is not None and not is_worker_alive(record) else None
        if not claim_run(conn, run_id, os.getpid(), utcnow(), stale_pid=stale_pid):
            raise WorkerError(f"{run_id} is already being run by another worker")
        claimed = True
        events.append("worker", mode=mode, pid=os.getpid())
        heartbeat.start()
        if snapshot.interrupts and mode != "resume":
            # The graph paused but the pause never reached the row (e.g. the worker died or was
            # stopped right after the checkpoint): surface it again instead of refusing to run.
            outcome = runner.settle(engine, snapshot)
        elif not snapshot.values:
            plan = deps.artifacts.read_plan()
            test_cmd = plan.test_cmd or config.project.test_cmd or ""
            outcome = runner.start(engine, graph, plan=plan, base_sha=record.base_sha, test_cmd=test_cmd)
        elif mode == "resume":
            outcome = runner.resume(engine, graph, decision or {})
        else:
            outcome = runner.continue_run(engine, graph)
        events.append("outcome", status=outcome.status)
        return outcome
    except WorkerError:
        raise
    except StopRequested:
        status = "stopped"
        try:
            kill_active_groups()
        except BaseException:
            _logger.warning("kill_active_groups failed while handling StopRequested", exc_info=True)
        if not claimed:
            return runner.RunOutcome(status=status)
        try:
            final_state = _record_terminal_state(conn, run_id, "stopped", "stopped by user")
            if final_state == "stopped":
                events.append("state", state="stopped", needs_attention="stopped by user")
            else:
                status = final_state
        except BaseException:
            _logger.warning("failed to record the stopped state for run %s", run_id, exc_info=True)
        return runner.RunOutcome(status=status)
    except Exception as exc:
        try:
            kill_active_groups()
        except BaseException:
            _logger.warning("kill_active_groups failed while handling a worker exception", exc_info=True)
        message = f"worker failed: {type(exc).__name__}: {exc}"[:500]
        if claimed:
            try:
                final_state = _record_terminal_state(conn, run_id, "failed", message)
                if final_state == "failed":
                    events.append("state", state="failed", needs_attention=message)
            except BaseException:
                _logger.warning("failed to record the failed state for run %s", run_id, exc_info=True)
        raise
    finally:
        if installed:
            try:
                signal.signal(signal.SIGTERM, previous_handler)
            except BaseException:
                _logger.warning("failed to restore the previous SIGTERM handler", exc_info=True)
        try:
            heartbeat.stop()
        except BaseException:
            _logger.warning("heartbeat failed to stop cleanly for run %s", run_id, exc_info=True)
        try:
            if claimed:
                release_run(conn, run_id, os.getpid())
        except BaseException:
            _logger.warning("failed to clear pid for run %s", run_id, exc_info=True)
        try:
            saver.conn.close()
        except BaseException:
            _logger.warning("failed to close the checkpointer connection for run %s", run_id, exc_info=True)
        try:
            conn.close()
        except BaseException:
            _logger.warning("failed to close the worker connection for run %s", run_id, exc_info=True)
