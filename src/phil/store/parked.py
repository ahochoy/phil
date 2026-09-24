import sqlite3

from phil.contracts import ParkedItem, Ref
from phil.store.db import utcnow


def _to_item(row: sqlite3.Row) -> ParkedItem:
    return ParkedItem(
        id=row["id"],
        raised_by=row["raised_by"],
        note=row["note"],
        why_not_now=row["why_not_now"],
        source=Ref(label=row["source_label"], path=row["source_path"]),
        status=row["status"],
        run_id=row["run_id"],
    )


def park(
    conn: sqlite3.Connection,
    *,
    raised_by: str,
    note: str,
    why_not_now: str,
    source: Ref,
    run_id: str | None = None,
) -> ParkedItem:
    conn.execute("BEGIN IMMEDIATE")
    try:
        count = conn.execute("SELECT COUNT(*) FROM parked").fetchone()[0]
        item_id = f"P-{count + 1:03d}"
        conn.execute(
            "INSERT INTO parked (id, raised_by, note, why_not_now, source_label, source_path,"
            " status, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (item_id, raised_by, note, why_not_now, source.label, source.path, run_id, utcnow()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _get(conn, item_id)


def _get(conn: sqlite3.Connection, item_id: str) -> ParkedItem:
    row = conn.execute("SELECT * FROM parked WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    return _to_item(row)


def list_parked(conn: sqlite3.Connection, status: str | None = "open") -> list[ParkedItem]:
    if status is None:
        rows = conn.execute("SELECT * FROM parked ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM parked WHERE status = ? ORDER BY id", (status,)).fetchall()
    return [_to_item(row) for row in rows]


def set_parked_status(conn: sqlite3.Connection, item_id: str, status: str) -> ParkedItem:
    cursor = conn.execute("UPDATE parked SET status = ? WHERE id = ?", (status, item_id))
    if cursor.rowcount == 0:
        raise KeyError(item_id)
    return _get(conn, item_id)


def open_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM parked WHERE status = 'open'").fetchone()[0]
