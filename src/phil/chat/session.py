from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from phil.contracts import Contract
from phil.store.artifacts import ArtifactStore
from phil.store.events import EventLog
from phil.store.paths import ProjectPaths


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

    def user(self, text: str, *, stage: str) -> None:
        self.transcript.append("user", stage=stage, text=text)

    def contract(self, kind: str, contract: Contract) -> None:
        self.transcript.append(kind, contract=contract.model_dump(mode="json"))

    def note(self, kind: str, **data: object) -> None:
        self.transcript.append(kind, **data)
