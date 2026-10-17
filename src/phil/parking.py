"""Closing parking-lot items, shared by `phil parked drop|done` and the chat's `/unpark`: what each
says back. Items are closed, never deleted, so the parking lot's numbering doesn't repeat."""

import sqlite3
from typing import Literal

from phil.store.parked import close_all_open, close_parked, parked_id
from phil.text import printable

Closing = Literal["dropped", "promoted"]
LIST_HINT = "phil parked lists them."
_DONE = {"dropped": "Dropped {}.", "promoted": "Marked {} done."}


def close_lines(conn: sqlite3.Connection, words: list[str], status: Closing, *, every: bool) -> tuple[list[str], bool]:
    """Close the items `words` names (or every open one) and say what happened. The flag is False
    when any word wasn't an open item, so the CLI can exit 1; the items that were open still close."""
    if every:
        closed = close_all_open(conn, status)
        return ([_DONE[status].format(", ".join(closed))] if closed else ["Parking lot is already empty."]), True
    ids, bad = [], []
    for word in words:
        item_id = parked_id(word)
        if item_id:
            ids.append(item_id)
        else:
            bad.append(word)
    closed, not_open = close_parked(conn, ids, status)
    lines = [_DONE[status].format(", ".join(closed))] if closed else []
    if not_open:
        lines.append(f"No open item {', '.join(not_open)}. {LIST_HINT}")
    lines += [f"Not an item ID: {printable(word)} (they look like P-001)." for word in bad]
    return lines, not (not_open or bad)
