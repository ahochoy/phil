"""How open chats are listed: one line per chat, and the hint that more than one is open."""

from datetime import UTC, datetime

from rich.text import Text

from phil.chat.session import ChatSummary
from phil.text import printable


def open_chat_line(n: int, chat: ChatSummary, width: int, *, now: datetime | None = None) -> Text:
    """`n. what it waits on · how long ago · goal`, cut to the width with … (spec 2026-10-16 §6.2).
    What it waits on: its run (`label · run failed`), a goal an error stopped (`planning failed`), or
    a plan (`plan to approve`). `open elsewhere` sits before the goal so a cut can't hide it (R23)."""
    from phil.ui.runs import label_for
    from phil.ui.when import ago

    line = Text(f"{n}. ")
    if chat.failed:
        line.append(f"{chat.failed} failed", "phil.warn")
        line.append(" · ")
    elif chat.run_id:
        line.append(label_for(chat.run_id, chat.run_name, first=False), "phil.id")
        line.append(f" · run {chat.run_state or 'unknown'} · ")
    elif chat.stage == "approval":
        line.append("plan to approve · ")
    if chat.active_at is not None:
        line.append(f"{ago(chat.active_at, now or datetime.now(UTC))} · ", "phil.muted")
    if chat.open_elsewhere:
        line.append("open elsewhere", "phil.warn")
        line.append(" · ")
    if chat.objective:
        line.append(printable(chat.objective))
    else:
        line.append("(no goal)", "phil.muted")
    line.truncate(max(width - 1, 1), overflow="ellipsis")
    return line


def open_chats_hint(count: int) -> str | None:
    """`2 open chats · /chat`; None when no chat is open."""
    if count <= 0:
        return None
    return f"{count} open chat{'s' if count != 1 else ''} · /chat"
