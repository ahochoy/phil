import sqlite3
import threading

import pytest

from phil.store import db
from phil.store.db import BUSY_TIMEOUT_S, MIGRATIONS, connect, retry_when_locked
from phil.store.runs import create_run, get_run, update_run


def user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_new_database_is_at_latest_version(tmp_path):
    conn = connect(tmp_path / "phil.db")
    assert user_version(conn) == len(MIGRATIONS)


def test_connect_is_idempotent(tmp_path):
    connect(tmp_path / "phil.db")
    conn = connect(tmp_path / "phil.db")
    assert user_version(conn) == len(MIGRATIONS)


def test_legacy_unversioned_database_upgrades(tmp_path):
    path = tmp_path / "phil.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(db.SCHEMA)
    legacy.close()
    conn = connect(path)
    assert user_version(conn) == len(MIGRATIONS)


def test_runs_table_has_chat_id_column(tmp_path):
    conn = connect(tmp_path / "phil.db")
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(runs)")]
    assert "chat_id" in columns


def test_new_migration_applies_once(tmp_path, monkeypatch):
    path = tmp_path / "phil.db"
    connect(path)
    monkeypatch.setattr(db, "MIGRATIONS", [*MIGRATIONS, "ALTER TABLE runs ADD COLUMN note TEXT"])
    conn = connect(path)
    connect(path)
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(runs)")]
    assert columns.count("note") == 1
    assert user_version(conn) == len(MIGRATIONS) + 1


def test_telemetry_table_has_the_new_observability_columns(tmp_path):
    conn = connect(tmp_path / "phil.db")
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(telemetry)")]
    for expected in ("chat_id", "model_calls", "tool_calls", "retries", "cost_source"):
        assert expected in columns


def test_calls_table_exists(tmp_path):
    conn = connect(tmp_path / "phil.db")
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(calls)")]
    for expected in ("id", "telemetry_id", "model", "input_tokens", "output_tokens", "cost_usd", "cost_source", "created_at"):
        assert expected in columns


def test_the_name_migration_runs_on_an_existing_database(tmp_path, monkeypatch):
    path = tmp_path / "phil.db"
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS[:-1])  # the database as it was before runs.name
    old = connect(path)
    old.execute(
        "INSERT INTO runs (run_id, keyword, base_sha, branch, worktree, state, tasks_total, created_at, updated_at)"
        " VALUES ('r-0001', 'CALC', 'abc', 'phil/r-0001', '/wt', 'pending', 1, 't', 't')"
    )
    old.close()
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS)
    conn = connect(path)
    assert user_version(conn) == len(MIGRATIONS)
    assert "name" in [row["name"] for row in conn.execute("PRAGMA table_info(runs)")]
    assert get_run(conn, "r-0001").name is None


def test_a_connection_waits_up_to_the_busy_timeout_for_a_lock(tmp_path):
    conn = connect(tmp_path / "phil.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_S * 1000


def locked() -> sqlite3.OperationalError:
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


def test_a_locked_write_is_tried_again_after_a_pause():
    calls, pauses = [], []

    def write():
        calls.append(1)
        if len(calls) < 3:
            raise locked()
        return "done"

    assert retry_when_locked(write, sleep=pauses.append) == "done"
    assert len(calls) == 3 and pauses == [db.LOCK_RETRY_PAUSE_S] * 2


def test_a_write_still_locked_after_every_try_raises():
    calls = []

    def write():
        calls.append(1)
        raise locked()

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        retry_when_locked(write, sleep=lambda _: None)
    assert len(calls) == db.LOCK_TRIES


def test_other_database_errors_are_not_tried_again():
    calls = []

    def write():
        calls.append(1)
        raise sqlite3.OperationalError("no such table: runs")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        retry_when_locked(write, sleep=lambda _: None)
    assert len(calls) == 1


def test_a_write_waits_for_another_connection_to_release_its_lock(tmp_path, monkeypatch):
    """End to end: a second connection holds the write lock past one busy wait; the write still lands."""
    path = tmp_path / "phil.db"
    conn = connect(path)
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=tmp_path / "wt", tasks_total=1)
    conn.execute("PRAGMA busy_timeout = 50")  # one busy wait is shorter than the hold below
    monkeypatch.setattr(db, "LOCK_RETRY_PAUSE_S", 0.5)  # the second try starts well after the release
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.15, lambda: holder.execute("COMMIT"))
    release.start()
    try:
        assert update_run(conn, "r-0001", current_node="implement").current_node == "implement"
    finally:
        release.join()
