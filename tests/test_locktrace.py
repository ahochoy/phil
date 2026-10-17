import sqlite3

import pytest

from tests.support import locktrace
from tests.support.locktrace import Traced, lock_report, open_since


def test_a_transaction_is_open_from_begin_until_commit_or_rollback():
    assert open_since([(1.0, "t", "SELECT 1")]) is None
    assert open_since([(1.0, "t", "BEGIN IMMEDIATE"), (2.0, "t", "INSERT INTO x VALUES (1)")]) == 1.0
    assert open_since([(1.0, "t", "BEGIN "), (2.0, "t", "COMMIT")]) is None
    assert open_since([(1.0, "t", "BEGIN"), (2.0, "t", "ROLLBACK"), (3.0, "t", "BEGIN")]) == 3.0
    assert open_since([(1.0, "t", "BEGIN IMMEDIATE"), (2.0, "t", "✗ database is locked")]) is None


def test_the_report_puts_the_connection_with_an_open_transaction_first():
    idle = Traced("worker.py:182", [(9.0, "MainThread", "UPDATE runs SET current_node = 'implement'")])
    holder = Traced("checkpoint.py:14", [(2.0, "ThreadPoolExecutor-1_0", "BEGIN"),
                                         (2.1, "ThreadPoolExecutor-1_0", "INSERT INTO writes VALUES (1)")])
    report = lock_report([idle, holder], now=12.5)
    first, *_ = report.splitlines()
    assert first == "#1 connection opened at checkpoint.py:14: transaction open for 10.50s, 2 statements"
    assert "#2 connection opened at worker.py:182: no transaction open, 1 statements" in report
    assert "-10.500s  ThreadPoolExecutor-1_0    BEGIN" in report


def test_a_real_lock_shows_its_holder(tmp_path, monkeypatch):
    """The traced connect on two real connections: one holds the write lock, the other's write gives up."""
    monkeypatch.setattr(locktrace, "_connections", [])
    path = tmp_path / "phil.db"
    holder = locktrace._connect(path, isolation_level=None)
    holder.execute("CREATE TABLE runs (n INTEGER)")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO runs VALUES (1)")
    waiter = locktrace._connect(path, timeout=0.05, isolation_level=None)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        waiter.execute("INSERT INTO runs VALUES (2)")
    report = lock_report(locktrace._connections, now=locktrace.time.monotonic())
    assert report.startswith("#1 connection opened at test_locktrace.py:")
    assert "transaction open for" in report.splitlines()[0]
    assert "INSERT INTO runs VALUES (2)" in report and "✗ database is locked" in report
