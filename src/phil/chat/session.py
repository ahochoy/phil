import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from phil.contracts import Contract
from phil.store.artifacts import ArtifactStore
from phil.store.events import EventLog
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run

STATE_FILE = "state.json"
CHAT_ID_RE = re.compile(r"^c-\d{8}-\d{6}(-\d+)?$")


@dataclass(frozen=True)
class ChatSummary:
    id: str
    objective: str | None
    stage: str
    run_id: str | None
    run_state: str | None


@dataclass
class ChatSession:
    id: str
    dir: Path
    artifacts: ArtifactStore
    transcript: EventLog

    @classmethod
    def create(cls, paths: ProjectPaths, *, now: Callable[[], datetime] = datetime.now) -> "ChatSession":
        base = f"c-{now().strftime('%Y%m%d-%H%M%S')}"
        chats = paths.project_dir / "chats"
        session_id, n = base, 1
        while (chats / session_id).exists():
            n += 1
            session_id = f"{base}-{n}"
        directory = chats / session_id
        directory.mkdir(parents=True)
        return cls(session_id, directory, ArtifactStore(directory), EventLog(directory / "transcript.jsonl"))

    @classmethod
    def open(cls, paths: ProjectPaths, chat_id: str) -> "ChatSession":
        if not CHAT_ID_RE.match(chat_id):
            raise ValueError(f"not a chat id: {chat_id!r}")
        directory = paths.project_dir / "chats" / chat_id
        if not directory.is_dir():
            raise FileNotFoundError(f"no chat {chat_id}")
        return cls(chat_id, directory, ArtifactStore(directory), EventLog(directory / "transcript.jsonl"))

    def user(self, text: str, *, stage: str) -> None:
        self.transcript.append("user", stage=stage, text=text)

    def contract(self, kind: str, contract: Contract) -> None:
        self.transcript.append(kind, contract=contract.model_dump(mode="json"))

    def note(self, kind: str, **data: object) -> None:
        self.transcript.append(kind, **data)

    def save_state(self, data: dict) -> None:
        target = self.dir / STATE_FILE
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, default=str))
        os.replace(tmp, target)

    def load_state(self) -> dict:
        try:
            return json.loads((self.dir / STATE_FILE).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def list_open_chats(paths: ProjectPaths, conn: sqlite3.Connection) -> list[ChatSummary]:
    chats = paths.project_dir / "chats"
    if not chats.is_dir():
        return []
    found: list[ChatSummary] = []
    for directory in sorted(chats.iterdir(), key=lambda d: d.name, reverse=True):
        if not directory.is_dir():
            continue
        state = ChatSession.open(paths, directory.name).load_state()
        run_id = state.get("run_id")
        open_run = bool(run_id) and not state.get("done_seen", False)
        open_plan = state.get("stage") == "approval" and bool(state.get("plan"))
        if not (open_run or open_plan):
            continue
        record = get_run(conn, run_id) if run_id else None
        goal = state.get("goal") or {}
        found.append(
            ChatSummary(directory.name, goal.get("objective"), state.get("stage", "idle"), run_id, record.state if record else None)
        )
    return found
