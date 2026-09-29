import sqlite3

from phil.store import db
from phil.store.db import MIGRATIONS, connect


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
