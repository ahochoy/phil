import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from phil import platform
from phil.contracts import Contract
from phil.store.artifacts import ArtifactStore
from phil.store.events import EventLog
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run

STATE_FILE = "state.json"
LOCK_FILE = "chat.lock"  # the pid of the process that has the chat open
LOG_FILE = "phil.log"
CHAT_ID_RE = re.compile(r"^c-\d{8}-\d{6}(-\d+)?$")


@dataclass(frozen=True)
class ChatSummary:
    id: str
    objective: str | None
    stage: str
    run_id: str | None
    run_state: str | None
    open_elsewhere: bool = False
    run_name: str | None = None
    active_at: datetime | None = None  # when the chat last saved its state (aware, UTC)
    failed: str | None = None  # "planning" or "answer": an error stopped the chat's goal there


FAILED_PHASES = ("planning", "answer")


def failed_goal(value: object) -> dict | None:
    """A saved `failed_goal` ({"text", "objective", "phase"}), or None when there's none or it doesn't
    read as one (hand-edited, or from a broken write)."""
    if not isinstance(value, dict):
        return None
    text, objective, phase = value.get("text"), value.get("objective"), value.get("phase")
    if not isinstance(text, str) or not text.strip() or phase not in FAILED_PHASES:
        return None
    if objective is not None and not isinstance(objective, str):
        return None
    return {"text": text, "objective": objective or None, "phase": phase}


class ChatLocked(Exception):
    """The chat is open in another live process."""

    def __init__(self, pid: int) -> None:
        super().__init__(pid)
        self.pid = pid


def _pid_alive(pid: int) -> bool:
    return platform.pid_alive(pid)


def lock_holder(directory: Path) -> int | None:
    """The pid of another live process holding this chat's lock, else None (no lock, stale, or ours)."""
    try:
        pid = int((directory / LOCK_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid() or not _pid_alive(pid):
        return None
    return pid


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
        tmp.write_text(json.dumps(data, default=str), encoding="utf-8", newline="\n")
        os.replace(tmp, target)

    def lock(self) -> None:
        """Hold this chat for this process; raises ChatLocked if another live process holds it.

        A lock left by a process that died is taken over.
        """
        holder = lock_holder(self.dir)
        if holder is not None:
            raise ChatLocked(holder)
        target = self.dir / LOCK_FILE
        tmp = self.dir / f"{LOCK_FILE}.{os.getpid()}.tmp"
        tmp.write_text(str(os.getpid()), encoding="utf-8", newline="\n")
        os.replace(tmp, target)

    def unlock(self) -> None:
        """Release the lock if this process holds it."""
        target = self.dir / LOCK_FILE
        try:
            if int(target.read_text(encoding="utf-8").strip()) == os.getpid():
                target.unlink()
        except (OSError, ValueError):
            pass

    def load_state(self) -> dict:
        try:
            return json.loads((self.dir / STATE_FILE).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def list_open_chats(paths: ProjectPaths, conn: sqlite3.Connection) -> list[ChatSummary]:
    chats = paths.project_dir / "chats"
    if not chats.is_dir():
        return []
    found: list[ChatSummary] = []
    for directory in sorted(chats.iterdir(), key=lambda d: d.name, reverse=True):
        if not directory.is_dir() or not CHAT_ID_RE.match(directory.name):
            continue
        state = ChatSession.open(paths, directory.name).load_state()
        if not isinstance(state, dict):
            continue  # unreadable: `phil --resume <id>` still reopens it (and starts fresh)
        run_id = state.get("run_id")
        open_run = bool(run_id) and not state.get("done_seen", False)
        open_plan = state.get("stage") == "approval" and bool(state.get("plan"))
        failed = None if (open_run or open_plan) else failed_goal(state.get("failed_goal"))
        if not (open_run or open_plan or failed):
            continue
        record = get_run(conn, run_id) if run_id else None
        goal = state.get("goal") if isinstance(state.get("goal"), dict) else {}
        objective = goal.get("objective")
        if failed is not None:
            objective = failed["objective"] or failed["text"]
        found.append(
            ChatSummary(
                directory.name, objective, state.get("stage", "idle"), run_id,
                record.state if record else None, open_elsewhere=lock_holder(directory) is not None,
                run_name=record.name if record else None, active_at=_saved_at(directory),
                failed=failed["phase"] if failed else None,
            )
        )
    # Most recently active first; a chat reopened today comes before one started yesterday.
    found.sort(key=lambda chat: (chat.active_at or datetime.min.replace(tzinfo=UTC), chat.id), reverse=True)
    return found


def _saved_at(directory: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp((directory / STATE_FILE).stat().st_mtime, UTC)
    except OSError:
        return None
