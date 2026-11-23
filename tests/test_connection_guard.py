import sqlite3
import threading

import pytest

from tests.conftest import close_leftover_connections


def _is_closed(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_a_connection_a_test_leaves_open_is_closed_after_it(monkeypatch, tmp_path):
    # Left open, each one holds a file descriptor or three (the database, its -wal and -shm)
    # until the garbage collector gets to it. A pytest worker piled up past 1024 of them on CI,
    # and prompt_toolkit's select() can't wait on a descriptor numbered that high, so the
    # terminal tests hung.
    gen = close_leftover_connections(monkeypatch)
    next(gen)
    conn = sqlite3.connect(tmp_path / "left-open.db")
    with pytest.raises(StopIteration):
        next(gen)
    assert _is_closed(conn)


def test_a_connection_still_in_use_by_a_live_thread_is_left_alone(monkeypatch, tmp_path):
    gen = close_leftover_connections(monkeypatch)
    next(gen)
    opened, release = threading.Event(), threading.Event()
    box: dict = {}

    def worker() -> None:
        box["conn"] = sqlite3.connect(tmp_path / "busy.db", check_same_thread=False)
        opened.set()
        release.wait(5)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    opened.wait(5)
    with pytest.raises(StopIteration):
        next(gen)
    try:
        assert not _is_closed(box["conn"])
    finally:
        release.set()
        thread.join(5)
        box["conn"].close()
