import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# How long a statement waits for another connection's lock before SQLite gives up (seconds). Every
# connection to a project's database uses it, the checkpointer's included.
BUSY_TIMEOUT_S = 30
# A write that still finds the database locked after a whole busy wait is tried this many times in
# all, with this pause between tries.
LOCK_TRIES = 3
LOCK_RETRY_PAUSE_S = 1.0

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

MIGRATIONS: list[str] = [
    SCHEMA,
    "ALTER TABLE telemetry ADD COLUMN call INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE runs ADD COLUMN chat_id TEXT",
    "ALTER TABLE telemetry ADD COLUMN chat_id TEXT",
    "ALTER TABLE telemetry ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE telemetry ADD COLUMN tool_calls TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE telemetry ADD COLUMN retries INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE telemetry ADD COLUMN cost_source TEXT NOT NULL DEFAULT 'reported'",
    "CREATE TABLE IF NOT EXISTS calls ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "telemetry_id INTEGER NOT NULL,"
    "model TEXT NOT NULL,"
    "input_tokens INTEGER NOT NULL,"
    "output_tokens INTEGER NOT NULL,"
    "cost_usd REAL NOT NULL,"
    "cost_source TEXT NOT NULL,"
    "created_at TEXT NOT NULL"
    ")",
    "ALTER TABLE runs ADD COLUMN base_branch TEXT",
    "ALTER TABLE runs ADD COLUMN pr_url TEXT",
    "ALTER TABLE runs ADD COLUMN pr_number INTEGER",
    "ALTER TABLE runs ADD COLUMN pr_state TEXT",
    "ALTER TABLE runs ADD COLUMN pr_checked_at TEXT",
    "ALTER TABLE runs ADD COLUMN config_overrides TEXT",  # the run's `--set` overrides, as a JSON list
    "ALTER TABLE runs ADD COLUMN depth TEXT",  # "quick" or "full"; NULL for runs before M3b
    "ALTER TABLE telemetry ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0",  # of input_tokens
    "ALTER TABLE runs ADD COLUMN name TEXT",  # display only (spec 2026-10-16 §3.2); NULL for older runs
    "ALTER TABLE telemetry ADD COLUMN reminders INTEGER NOT NULL DEFAULT 0",  # spec 2026-10-22 §4.2
]


def _statements(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _migrate(conn: sqlite3.Connection) -> None:
    while True:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version >= len(MIGRATIONS):
                conn.execute("COMMIT")
                return
            for statement in _statements(MIGRATIONS[version]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def retry_when_locked[T](write: Callable[[], T], *, sleep: Callable[[float], None] = time.sleep) -> T:
    """`write()`, tried again after a pause while another connection still holds the database
    lock past a whole busy wait (a slow disk on a loaded machine). Other errors raise at once."""
    for attempt in range(1, LOCK_TRIES + 1):
        try:
            return write()
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY or attempt == LOCK_TRIES:
                raise
            sleep(LOCK_RETRY_PAUSE_S)
    raise AssertionError("unreachable")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn
