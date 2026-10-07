import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime

from phil.chat.events import ChatEvent
from phil.run.launch import is_worker_alive, worker_starting
from phil.store.activity import activity_log
from phil.store.db import connect
from phil.store.events import MILESTONE_KINDS, run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.store.telemetry import run_totals, run_usage
from phil.ui.feed_view import _int_seq, _seconds, interleave

logger = logging.getLogger(__name__)

# These end the run outright, regardless of any worker.
ENDED_UNCONDITIONALLY = ("completed", "incomplete", "aborted", "cleaned")
# These only end the watch once no worker is alive or starting a resume for it — a `/resume`
# just kicked off can still see the row's stale `failed`/`stopped` state for a moment.
ENDED_IF_IDLE = ("failed", "stopped")

CONSECUTIVE_FAILURES_BEFORE_REPORT = 5
# Run events posted to the chat as they are, once each: the latest of each kind since the last poll.
NOTICES = ("budget_warning", "test_cmd_changed")


def _live_of(record: dict) -> dict:
    """The live row's step for a tool call's start record; `started` falls back to now on a bad `ts`.
    `seq` says which call it is, so only that call's end clears it (the chat ignores it)."""
    started = _seconds(record.get("ts")) if record.get("ts") else None
    if started is None:
        started = time.time()
    return {"task": record.get("task"), "role": record.get("role"), "summary": record.get("summary", ""),
            "started": started, "seq": record.get("seq")}


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
        # The feed starts at the current end of both logs: a reopened chat shows only what's new,
        # except a tool call still running, which can seed the live row at the first poll (_seed_live).
        self.activity = activity_log(paths, run_id)
        self._event_offset = self.events.end_offset()
        self._activity_offset = self.activity.end_offset()
        self._seed: dict | None = self.activity.pending()
        self._open: dict[int, dict] = {}  # calls started and not yet ended, by seq, across polls
        self._live: dict = {}
        self._live_posted: dict = {}  # the chat starts with no live step
        self.done = False
        self._last: tuple | None = None
        self._paused_ts: str | None = None
        self._paused = False
        self._idle_since: float | None = None
        self._lost_posted = False
        # Seeded from the log so a reopened/resumed chat's new watcher posts only the notices
        # (budget warnings, test command switches) written after it started, not ones already shown.
        self._notice_ts: dict[str, str | None] = {kind: self._latest_ts(kind) for kind in NOTICES}
        self._consecutive_failures = 0
        self._error_posted = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _latest_ts(self, kind: str) -> str | None:
        try:
            latest = self.events.latest(kind)
        except Exception:  # an unreadable log: poll_once reports it through its own error path
            logger.debug("couldn't read the %s events for %s", kind, self.run_id, exc_info=True)
            return None
        return latest.get("ts") if latest else None

    def poll_once(self) -> None:
        if self.done:
            return
        conn = connect(self.paths.db_path)
        try:
            record = get_run(conn, self.run_id)
            if self._seed is not None:
                self._seed_live(record)
            self._poll_feed()
            if record is None:
                return
            snapshot = (record.current_node, record.state, record.tasks_done, record.tasks_total)
            if snapshot != self._last:
                self._last = snapshot
                started = datetime.fromisoformat(record.created_at).timestamp()
                totals = run_usage(conn, self.run_id)
                self.post(ChatEvent("run_progress", {
                    "node": record.current_node, "state": record.state, "tasks_done": record.tasks_done,
                    "tasks_total": record.tasks_total, "keyword": record.keyword, "started": started,
                    "tokens": totals.tokens, "cost_usd": totals.cost_usd, "cost_source": totals.cost_source,
                }))
            for kind in NOTICES:
                latest_notice = self.events.latest(kind)
                if latest_notice and latest_notice.get("ts") != self._notice_ts[kind]:
                    self._notice_ts[kind] = latest_notice.get("ts")
                    data = {k: v for k, v in latest_notice.items() if k not in ("kind", "ts")}
                    self.post(ChatEvent(kind, data))
            active = self.alive(record) or self.starting(self.events)
            if record.state == "escalated":
                latest = self.events.latest("escalation")
                if latest and not active and latest.get("ts") != self._paused_ts:
                    self._paused_ts, self._paused = latest.get("ts"), True
                    self.post(ChatEvent("run_paused", {"escalation": latest["escalation"]}))
            elif self._paused:
                self._paused = False
                self.post(ChatEvent("run_resumed", {}))
            ended = record.state in ENDED_UNCONDITIONALLY or (record.state in ENDED_IF_IDLE and not active)
            if ended:
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

    def _poll_feed(self) -> None:
        """Post the milestones and tool calls written since the last poll, in order, and the live step
        if it changed.

        A milestone carries the activity log's last seq when it was written: the tool records up to that
        seq are posted before it, the rest after it (a milestone without one goes in its file position).
        """
        new_events, self._event_offset = self.events.read(self._event_offset)
        records, self._activity_offset = self.activity.read(self._activity_offset)
        milestones = [event for event in new_events if event.get("kind") in MILESTONE_KINDS]
        for item, value in interleave(milestones, records):
            if item == "records":
                self._post_records(value)
            else:
                self.post(ChatEvent("milestone", value))
        spawns = [event for event in new_events if event.get("kind") == "spawn"]
        spawned = None
        if spawns:
            # A new worker: the previous worker's unfinished calls are dead, so none of them is live.
            self._open.clear()
            self._live = {}
            spawned = _seconds(spawns[-1].get("ts"))
        self._follow_live(records, spawned)
        if self._live != self._live_posted:
            self._live_posted = dict(self._live)
            self.post(ChatEvent("live_step", dict(self._live)))

    def _post_records(self, records: list[dict]) -> None:
        # Only end records make tool lines: a batch of starts alone would print nothing, and posting
        # it would only wake the prompt (the live step reports a start).
        if any(record.get("phase") == "end" for record in records):
            self.post(ChatEvent("activity", {"records": records}))

    def _seed_live(self, record) -> None:
        """At the first poll: the tool call still running when the watcher started becomes the live
        step, but only if the run's worker is alive and the call started after the latest spawn (a
        dead worker's unfinished call, or one from before a `/resume`, is not running)."""
        seed, self._seed = self._seed, None
        if seed is None or record is None or not self.alive(record):
            return
        spawn = self.events.latest("spawn")
        started, spawned = _seconds(seed.get("ts")), _seconds(spawn.get("ts")) if spawn else None
        if spawned is not None and (started is None or started <= spawned):
            return
        self._live = _live_of(seed)
        if _int_seq(seed) is not None:
            self._open[_int_seq(seed)] = seed

    def _follow_live(self, records: list[dict], spawned: float | None = None) -> None:
        """The live step is the newest call started and not yet ended, kept across polls: when it
        ends, the newest call still open (an outer sub-agent, say) takes its place, or none. With
        `spawned` (a spawn read in this poll), a start from before it belongs to the dead worker."""
        for record in records:
            seq = _int_seq(record)
            if seq is None:
                continue
            if record.get("phase") == "start":
                started = _seconds(record.get("ts"))
                if spawned is not None and (started is None or started <= spawned):
                    continue
                self._open[seq] = record
            elif record.get("phase") == "end":
                self._open.pop(seq, None)
        if not self._open:
            self._live = {}
            return
        newest = max(self._open)
        if self._live.get("seq") != newest:  # unchanged: keep its `started`, so it isn't reposted
            self._live = _live_of(self._open[newest])

    def rearm(self) -> None:
        """Re-post the current escalation if the run is still paused with no worker at the next poll.

        The chat calls this after spawning a resume worker: a worker that exits before claiming the
        row leaves the same escalation in place, and the chat must ask its question again.
        """
        self._paused_ts = None

    def _tick(self) -> None:
        try:
            self.poll_once()
        except Exception as exc:  # a transient read error must not kill the watch
            self._consecutive_failures += 1
            logger.warning("run watcher poll failed for run %s", self.run_id, exc_info=True)
            if self._consecutive_failures >= CONSECUTIVE_FAILURES_BEFORE_REPORT and not self._error_posted:
                self._error_posted = True
                self.post(ChatEvent("watch_error", {"error": f"{type(exc).__name__}: {exc}"}))
        else:
            self._consecutive_failures = 0
            self._error_posted = False

    def _run(self) -> None:
        while not self._stop.is_set() and not self.done:
            self._tick()
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"watch-{self.run_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
