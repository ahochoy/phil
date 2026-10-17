"""Pytest plugin, on when `PHIL_LOCKTRACE` is set (CI sets it): it traces every SQLite statement,
per connection, and when a test fails on "database is locked" it adds a section to the failure
saying which connections had a transaction open at the time, for how long, and what each ran last.

SQLite can't say who holds a lock, so the trace stands in: the holder is the connection with a
transaction still open (or the last one to start a write) when the waiting write gave up.
"""

import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_real_connect = sqlite3.connect
TAIL = 12  # statements shown per connection
WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")


@dataclass
class Traced:
    tag: str  # where it was opened: `checkpoint.py:14`
    statements: list[tuple[float, str, str]] = field(default_factory=list)  # (time, thread, statement)


_connections: list[Traced] = []
_guard = threading.Lock()


def _opened_at() -> str:
    """The innermost Phil source line that opened the connection, else the innermost test line."""
    frames = [f for f in reversed(traceback.extract_stack()) if f.filename != __file__]
    for part in ("/src/phil/", "/tests/"):
        for frame in frames:
            if part in frame.filename.replace("\\", "/"):
                return f"{Path(frame.filename).name}:{frame.lineno}"
    return "?"


class _Connection(sqlite3.Connection):
    """Marks a statement that failed (`✗ database is locked`), so the waiting connection, whose
    `BEGIN` never got the lock, isn't mistaken for a holder."""

    traced: "Traced"

    def execute(self, sql, *args, **kwargs):
        try:
            return super().execute(sql, *args, **kwargs)
        except sqlite3.Error as exc:
            self.traced.statements.append((time.monotonic(), threading.current_thread().name, f"✗ {exc}"))
            raise


def _connect(*args, **kwargs):
    conn = _real_connect(*args, factory=_Connection, **kwargs)
    traced = Traced(_opened_at())
    conn.traced = traced
    with _guard:
        _connections.append(traced)

    def trace(statement: str) -> None:
        traced.statements.append((time.monotonic(), threading.current_thread().name, " ".join(statement.split())))

    conn.set_trace_callback(trace)
    return conn


def open_since(statements: list[tuple[float, str, str]]) -> float | None:
    """When the connection's open transaction began, or None when none is open. A transaction opens
    at `BEGIN` and ends at `COMMIT`/`ROLLBACK`; a `BEGIN` that failed (`✗` next) never opened."""
    began = None
    last_word = ""
    for at, _, statement in statements:
        word = statement.split(" ", 1)[0].upper()
        if word == "BEGIN":
            began = at
        elif word in {"COMMIT", "ROLLBACK", "END"} or (word == "✗" and last_word == "BEGIN"):
            began = None
        last_word = word
    return began


def lock_report(connections: list[Traced], now: float) -> str:
    """Connections with an open transaction first, oldest first; then the rest by latest write."""
    if not connections:
        return "No SQLite connections were opened."

    def last_write(traced: Traced) -> float:
        writes = [at for at, _, st in traced.statements if st.split(" ", 1)[0].upper() in WRITES]
        return writes[-1] if writes else float("-inf")

    open_ones = sorted((c for c in connections if open_since(c.statements) is not None),
                       key=lambda c: open_since(c.statements))
    rest = sorted((c for c in connections if c not in open_ones), key=last_write, reverse=True)
    lines = []
    for number, traced in enumerate([*open_ones, *rest], start=1):
        began = open_since(traced.statements)
        state = f"transaction open for {now - began:.2f}s" if began is not None else "no transaction open"
        lines.append(f"#{number} connection opened at {traced.tag}: {state}, {len(traced.statements)} statements")
        for at, thread, statement in traced.statements[-TAIL:]:
            lines.append(f"    {at - now:+9.3f}s  {thread:<24.24}  {statement[:110]}")
    return "\n".join(lines)


def pytest_configure(config):
    sqlite3.connect = _connect


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    with _guard:
        _connections.clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed and call.excinfo is not None and "database is locked" in str(call.excinfo.value):
        with _guard:
            connections = list(_connections)
        report.sections.append(("SQLite lock trace", lock_report(connections, time.monotonic())))
