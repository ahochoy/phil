import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    branch TEXT NOT NULL,
    worktree TEXT NOT NULL,
    state TEXT NOT NULL,
    current_node TEXT,
    tasks_done INTEGER NOT NULL DEFAULT 0,
    tasks_total INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    heartbeat_at TEXT,
    needs_attention TEXT,
    story_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    layer TEXT NOT NULL,
    node TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    packet_tokens INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parked (
    id TEXT PRIMARY KEY,
    raised_by TEXT NOT NULL,
    note TEXT NOT NULL,
    why_not_now TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
