import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Console
from rich.markup import escape

from phil.run.launch import is_worker_alive, worker_starting
from phil.store.events import EventLog
from phil.store.runs import RunRecord, get_run

TERMINAL = ("completed", "aborted", "cleaned")


@dataclass
class AttachIO:
    choose: Callable[[str, list[str]], str]
    ask_hint: Callable[[], str | None]
    spawn: Callable[[str, dict | None], object]
    sleep: Callable[[float], None] = time.sleep
    alive: Callable[[RunRecord], bool] = field(default=is_worker_alive)
    starting: Callable[[EventLog], bool] = field(default=worker_starting)


def render_event(console: Console, event: dict) -> None:
    kind = event["kind"]
    if kind == "node":
        console.print(f"[phil.muted]· {escape(str(event['node']))}[/]")
    elif kind == "state":
        note = f" — {escape(event['needs_attention'])}" if event.get("needs_attention") else ""
        console.print(f"state: [phil.id]{escape(event['state'])}[/]{note}")
    elif kind == "escalation":
        escalation = event["escalation"]
        console.print(f"[phil.warn]⏸ {escape(escalation['summary'])}[/]")
        if escalation.get("error"):
            console.print(f"[phil.error]{escape(escalation['error'])}[/]")
    elif kind == "worker":
        console.print(f"[phil.muted]worker {event.get('pid')} ({escape(str(event.get('mode')))})[/]")
    elif kind == "spawn":
        console.print(f"[phil.muted]spawning worker {event.get('pid')} ({escape(str(event.get('mode')))})[/]")
    elif kind == "outcome":
        console.print(f"[phil.muted]worker finished: {escape(str(event.get('status')))}[/]")


def _prompt(escalation: dict) -> str:
    return f"{escalation['summary']} — what next"


def _exited(proc: object) -> bool:
    """True if `proc` (what `io.spawn` returned) is a process that has already exited.

    Polling also reaps our own finished child, so its pid stops reading as a live `spawn`.
    """
    poll = getattr(proc, "poll", None)
    return callable(poll) and poll() is not None


def _worker_ran(events: EventLog, proc: object) -> bool:
    pid = getattr(proc, "pid", None)
    return pid is not None and any(e["kind"] == "worker" and e.get("pid") == pid for e in events.read()[0])


def attach(
    conn: sqlite3.Connection,
    run_id: str,
    events: EventLog,
    console: Console,
    io: AttachIO,
    *,
    poll_s: float = 1.0,
    start_timeout_s: float = 30.0,
) -> str:
    offset = 0
    idle_since = time.monotonic()
    proc: object = None

    def active(record: RunRecord) -> bool:
        if proc is not None:
            _exited(proc)
        return io.alive(record) or io.starting(events)

    while True:
        new, offset = events.read(offset)
        for event in new:
            render_event(console, event)
        record = get_run(conn, run_id)
        assert record is not None
        if record.state in TERMINAL:
            console.print(f"[phil.muted]Summary: {escape(str(events.path.parent / 'summary.md'))}[/]")
            return record.state
        alive = active(record)
        if record.state == "escalated" and not alive:
            latest = events.latest("escalation")
            escalation = latest["escalation"] if latest else {"summary": record.needs_attention or "", "options": ["abort"]}
            action = io.choose(_prompt(escalation), escalation["options"])
            decision: dict = {"action": action}
            if action == "retry":
                hint = io.ask_hint()
                if hint:
                    decision["hint"] = hint
            current = get_run(conn, run_id)
            assert current is not None
            if current.state != "escalated" or active(current):
                console.print("[phil.muted]the run moved on; not resuming[/]")
                continue
            proc = io.spawn("resume", decision)
            deadline = time.monotonic() + start_timeout_s
            log_path = escape(str(events.path.parent / "logs" / "worker.log"))
            while get_run(conn, run_id).state == "escalated":
                if _exited(proc):
                    if _worker_ran(events, proc):
                        break  # it resumed and paused again before we sampled the row
                    console.print(f"[phil.warn]The worker exited without resuming the run; see {log_path}[/]")
                    return "escalated"
                if time.monotonic() > deadline:
                    console.print(f"[phil.warn]The worker did not start; see {log_path}[/]")
                    return "escalated"
                io.sleep(poll_s)
            idle_since = time.monotonic()
            continue
        if record.state in ("failed", "stopped"):
            console.print(f"Continue with `phil resume {escape(run_id)}`.")
            return record.state
        if record.state in ("running", "pending") and not alive:
            if time.monotonic() - idle_since > start_timeout_s:
                console.print(f"[phil.warn]No worker is running.[/] Continue with `phil resume {escape(run_id)}`.")
                return record.state
        else:
            idle_since = time.monotonic()
        io.sleep(poll_s)
