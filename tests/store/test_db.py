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


def test_new_migration_applies_once(tmp_path, monkeypatch):
    path = tmp_path / "phil.db"
    connect(path)
    monkeypatch.setattr(db, "MIGRATIONS", [*MIGRATIONS, "ALTER TABLE runs ADD COLUMN note TEXT"])
    conn = connect(path)
    connect(path)
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(runs)")]
    assert columns.count("note") == 1
    assert user_version(conn) == len(MIGRATIONS) + 1
