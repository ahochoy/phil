import json
from pathlib import Path

from phil.store.db import utcnow
from phil.store.paths import ProjectPaths

# The run milestones the activity feed shows between tool lines (each carries the activity `seq`).
MILESTONE_KINDS = ("task_started", "gate", "attempt_failed", "task_done", "verdict")


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, kind: str, **data: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": utcnow(), "kind": kind, **data}, default=str)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    def read(self, offset: int = 0) -> tuple[list[dict], int]:
        if not self.path.exists():
            return [], offset
        with self.path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
        end = data.rfind(b"\n") + 1
        events = []
        for line in data[:end].decode("utf-8", errors="replace").splitlines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a malformed complete line: skip it, keep reading
        return events, offset + end

    def latest(self, kind: str) -> dict | None:
        events, _ = self.read()
        for event in reversed(events):
            if event["kind"] == kind:
                return event
        return None


def test_cmd_changed_line(cmd: str) -> str:
    """What the chat and `phil attach` print for a `test_cmd_changed` event (escape before printing)."""
    return f"Using the updated test command: {cmd}."


test_cmd_changed_line.__test__ = False  # not a pytest test


def run_events(paths: ProjectPaths, run_id: str) -> EventLog:
    return EventLog(paths.run_dir(run_id) / "events.jsonl")
