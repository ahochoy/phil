import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from phil.store.db import utcnow

RunState = Literal["pending", "running", "paused", "escalated", "completed", "failed", "aborted"]

_UPDATABLE = {
    "state",
    "current_node",
    "tasks_done",
    "tasks_total",
    "pid",
    "heartbeat_at",
    "needs_attention",
}


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    keyword: str
    base_sha: str
    branch: str
    worktree: str
    state: str
    current_node: str | None
    tasks_done: int
    tasks_total: int
    pid: int | None
    heartbeat_at: str | None
    needs_attention: str | None
    story_ref: str | None
    created_at: str
    updated_at: str


def new_run_id(conn: sqlite3.Connection) -> str:
    while True:
        run_id = f"r-{secrets.token_hex(2)}"
        if get_run(conn, run_id) is None:
            return run_id


def create_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    keyword: str,
    base_sha: str,
    worktree: Path,
    tasks_total: int,
    story_ref: str | None = None,
) -> RunRecord:
    now = utcnow()
    conn.execute(
        "INSERT INTO runs (run_id, keyword, base_sha, branch, worktree, state, tasks_total,"
        " story_ref, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
        (run_id, keyword, base_sha, f"phil/{run_id}", str(worktree), tasks_total, story_ref, now, now),
    )
    run = get_run(conn, run_id)
    assert run is not None
    return run


def get_run(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return RunRecord(**dict(row)) if row else None


def list_runs(conn: sqlite3.Connection) -> list[RunRecord]:
    rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC").fetchall()
    return [RunRecord(**dict(row)) for row in rows]


def update_run(conn: sqlite3.Connection, run_id: str, **fields: object) -> RunRecord:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"cannot update run fields: {sorted(unknown)}")
    assignments = ", ".join(f"{name} = ?" for name in fields)
    cursor = conn.execute(
        f"UPDATE runs SET {assignments}, updated_at = ? WHERE run_id = ?",
        (*fields.values(), utcnow(), run_id),
    )
    if cursor.rowcount == 0:
        raise KeyError(run_id)
    run = get_run(conn, run_id)
    assert run is not None
    return run
