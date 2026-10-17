"""How screens name a run (spec 2026-10-16 §3.3): `newsletter-cta (r-4f1c)` on its first mention
in a chat, `newsletter-cta` after that, and the ID for a run with no name. Command hints always
print the ID (`phil resume r-4f1c`), never a label."""

from typing import Protocol


class Named(Protocol):
    run_id: str
    name: str | None


def label_for(run_id: str, name: str | None, *, first: bool) -> str:
    if not name:
        return run_id
    return f"{name} ({run_id})" if first else name


def run_label(record: Named, *, first: bool) -> str:
    return label_for(record.run_id, record.name, first=first)
