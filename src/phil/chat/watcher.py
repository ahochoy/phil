import threading
import time
from collections.abc import Callable
from datetime import datetime

from phil.chat.events import ChatEvent
from phil.run.launch import is_worker_alive, worker_starting
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.store.telemetry import run_totals

ENDED = ("completed", "aborted", "cleaned", "failed", "stopped")


class RunWatcher:
    """Follows one run for its chat: tails the run's event log and row, posts what changed."""

    def __init__(
        self,
        paths: ProjectPaths,
        run_id: str,
        post: Callable[[ChatEvent], None],
        *,
        alive=is_worker_alive,
        starting=worker_starting,
        clock: Callable[[], float] = time.time,
        lost_after_s: float = 30.0,
        interval_s: float = 1.0,
    ) -> None:
        self.paths, self.run_id, self.post = paths, run_id, post
        self.alive, self.starting, self.clock = alive, starting, clock
        self.lost_after_s, self.interval_s = lost_after_s, interval_s
        self.events = run_events(paths, run_id)
        self.done = False
        self._last: tuple | None = None
        self._paused_ts: str | None = None
        self._paused = False
        self._idle_since: float | None = None
        self._lost_posted = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> None:
        if self.done:
            return
        conn = connect(self.paths.db_path)
        try:
            record = get_run(conn, self.run_id)
            if record is None:
                return
            snapshot = (record.current_node, record.state, record.tasks_done, record.tasks_total)
            if snapshot != self._last:
                self._last = snapshot
                started = datetime.fromisoformat(record.created_at).timestamp()
                self.post(ChatEvent("run_progress", {
                    "node": record.current_node, "state": record.state, "tasks_done": record.tasks_done,
                    "tasks_total": record.tasks_total, "keyword": record.keyword, "started": started,
                }))
            active = self.alive(record) or self.starting(self.events)
            if record.state == "escalated":
                latest = self.events.latest("escalation")
                if latest and not active and latest.get("ts") != self._paused_ts:
                    self._paused_ts, self._paused = latest.get("ts"), True
                    self.post(ChatEvent("run_paused", {"escalation": latest["escalation"]}))
            elif self._paused:
                self._paused = False
                self.post(ChatEvent("run_resumed", {}))
            if record.state in ENDED:
                tokens, cost = run_totals(conn, self.run_id)
                self.done = True
                self.post(ChatEvent("run_done", {
                    "state": record.state, "tasks_done": record.tasks_done, "tasks_total": record.tasks_total,
                    "needs_attention": record.needs_attention, "tokens": tokens, "cost_usd": cost,
                    "summary": str(self.paths.run_dir(self.run_id) / "summary.md"),
                }))
                return
            if record.state in ("running", "pending") and not active:
                now = self.clock()
                self._idle_since = self._idle_since or now
                if now - self._idle_since > self.lost_after_s and not self._lost_posted:
                    self._lost_posted = True
                    self.post(ChatEvent("worker_lost", {}))
            else:
                self._idle_since, self._lost_posted = None, False
        finally:
            conn.close()

    def _run(self) -> None:
        while not self._stop.is_set() and not self.done:
            try:
                self.poll_once()
            except Exception:  # a transient read error must not kill the watch
                pass
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"watch-{self.run_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
