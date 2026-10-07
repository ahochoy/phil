import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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


class ChatLocked(Exception):
    """The chat is open in another live process."""

    def __init__(self, pid: int) -> None:
        super().__init__(pid)
        self.pid = pid


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":  # SPIKE: os.kill(pid, 0) is CTRL_C_EVENT on Windows
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by someone else
    except OSError:
        return False
    return True


def lock_holder(directory: Path) -> int | None:
    """The pid of another live process holding this chat's lock, else None (no lock, stale, or ours)."""
    try:
        pid = int((directory / LOCK_FILE).read_text().strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid() or not _pid_alive(pid):
        return None
    return pid


@contextmanager
def chat_logging(directory: Path) -> Iterator[None]:
    """Send the `phil` loggers to `<chat dir>/phil.log` and nowhere else while the chat runs.

    Worker threads (the run watcher) log warnings with tracebacks; with no handler, Python's
    last-resort handler would write them to stderr, above the prompt.
    """
    logger = logging.getLogger("phil")
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(directory / LOG_FILE, delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    propagate = logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate
        handler.close()


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

    def lock(self) -> None:
        """Hold this chat for this process; raises ChatLocked if another live process holds it.

        A lock left by a process that died is taken over.
        """
        holder = lock_holder(self.dir)
        if holder is not None:
            raise ChatLocked(holder)
        target = self.dir / LOCK_FILE
        tmp = self.dir / f"{LOCK_FILE}.{os.getpid()}.tmp"
        tmp.write_text(str(os.getpid()))
        os.replace(tmp, target)

    def unlock(self) -> None:
        """Release the lock if this process holds it."""
        target = self.dir / LOCK_FILE
        try:
            if int(target.read_text().strip()) == os.getpid():
                target.unlink()
        except (OSError, ValueError):
            pass

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
        if not directory.is_dir() or not CHAT_ID_RE.match(directory.name):
            continue
        state = ChatSession.open(paths, directory.name).load_state()
        if not isinstance(state, dict):
            continue  # unreadable: `phil --resume <id>` still reopens it (and starts fresh)
        run_id = state.get("run_id")
        open_run = bool(run_id) and not state.get("done_seen", False)
        open_plan = state.get("stage") == "approval" and bool(state.get("plan"))
        if not (open_run or open_plan):
            continue
        record = get_run(conn, run_id) if run_id else None
        goal = state.get("goal") if isinstance(state.get("goal"), dict) else {}
        found.append(
            ChatSummary(
                directory.name, goal.get("objective"), state.get("stage", "idle"), run_id,
                record.state if record else None, open_elsewhere=lock_holder(directory) is not None,
            )
        )
    return found
