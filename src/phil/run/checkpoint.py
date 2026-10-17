import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from phil.store.db import BUSY_TIMEOUT_S


def open_checkpointer(db_path: Path) -> SqliteSaver:
    """The run graph's checkpointer, on the project's database. It waits for a lock as long as
    Phil's own connections do; it keeps sqlite3's default transactions, so each batch of writes it
    saves still commits as one."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S, check_same_thread=False))
