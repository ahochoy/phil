import json
from pathlib import Path

from phil.store.db import utcnow
from phil.store.paths import ProjectPaths


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, kind: str, **data: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": utcnow(), "kind": kind, **data}, default=str)
        with self.path.open("a") as handle:
            handle.write(line + "\n")

    def read(self, offset: int = 0) -> tuple[list[dict], int]:
        if not self.path.exists():
            return [], offset
        with self.path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
        end = data.rfind(b"\n") + 1
        events = [json.loads(line) for line in data[:end].decode().splitlines() if line]
        return events, offset + end

    def latest(self, kind: str) -> dict | None:
        events, _ = self.read()
        for event in reversed(events):
            if event["kind"] == kind:
                return event
        return None


def run_events(paths: ProjectPaths, run_id: str) -> EventLog:
    return EventLog(paths.run_dir(run_id) / "events.jsonl")
