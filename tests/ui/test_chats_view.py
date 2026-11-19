from datetime import UTC, datetime

from phil.chat.session import ChatSummary
from phil.ui.chats_view import open_chat_line, open_chats_hint


def test_open_chats_hint():
    assert open_chats_hint(0) is None
    assert open_chats_hint(1) == "1 open chat · /chat"
    assert open_chats_hint(2) == "2 open chats · /chat"


def test_open_chat_line_is_cut_to_the_width():
    now = datetime(2026, 11, 18, tzinfo=UTC)
    chat = ChatSummary("c-1", "Add a very long goal that will not fit", "approval", None, None)
    line = open_chat_line(1, chat, 20, now=now).plain
    assert line.startswith("1. plan to approve") and line.endswith("…") and len(line) <= 19
