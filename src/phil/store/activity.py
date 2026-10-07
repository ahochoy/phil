"""A run's tool-call activity (spec 2026-10-07 activity feed): one JSON line when a tool call
starts and one when it ends, in `runs/<id>/activity.jsonl`, with what's behind a `#n` reference
in `runs/<id>/activity/<seq>.txt`. Recording never fails a run: the first failed write logs a
warning and disables the log for the rest of this process."""

import json
import logging
import threading
from pathlib import Path

from phil.store.db import utcnow
from phil.store.paths import ProjectPaths

logger = logging.getLogger(__name__)

ACTIVITY_FILE = "activity.jsonl"
DETAIL_DIR = "activity"
MAX_DETAIL_CHARS = 200_000


def _parse(data: bytes) -> list[dict]:
    records = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("skipping a malformed activity line")
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


class ActivityLog:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.path = run_dir / ACTIVITY_FILE
        self._lock = threading.Lock()
        self._seq: int | None = None
        self.disabled = False

    # --- writing ---------------------------------------------------------------------------

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._current_seq()

    def _current_seq(self) -> int:
        if self._seq is None:
            records, _ = self.read()
            self._seq = max((int(r.get("seq", 0)) for r in records), default=0)
        return self._seq

    def _append(self, record: dict) -> bool:
        if self.disabled:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
            return True
        except Exception:
            self.disabled = True
            logger.warning("activity log disabled for %s: a write failed", self.run_dir, exc_info=True)
            return False

    def start(self, *, task: str | None, role: str, tool: str, summary: str) -> int | None:
        with self._lock:
            if self.disabled:
                return None
            try:
                seq = self._current_seq() + 1
            except Exception:
                self.disabled = True
                logger.warning("activity log disabled for %s: a read failed", self.run_dir, exc_info=True)
                return None
            record = {"seq": seq, "ts": utcnow(), "phase": "start", "task": task, "role": role,
                      "tool": tool, "summary": summary}
            if not self._append(record):
                return None
            self._seq = seq
            return seq

    def end(self, seq: int | None, *, task: str | None, role: str, tool: str, summary: str, result: str,
            ok: bool, detail: str | None, duration_ms: int) -> None:
        if seq is None:
            return
        with self._lock:
            name = self._write_detail(seq, detail) if detail else None
            self._append({"seq": seq, "ts": utcnow(), "phase": "end", "task": task, "role": role, "tool": tool,
                          "summary": summary, "duration_ms": duration_ms, "result": result, "ok": ok,
                          "detail": name})

    def record(self, *, task: str | None, role: str, tool: str, summary: str, result: str, ok: bool,
               detail: str | None, duration_ms: int) -> int | None:
        """A call that already finished (the engine's own test runs): start and end together."""
        seq = self.start(task=task, role=role, tool=tool, summary=summary)
        self.end(seq, task=task, role=role, tool=tool, summary=summary, result=result, ok=ok,
                 detail=detail, duration_ms=duration_ms)
        return seq

    def _write_detail(self, seq: int, text: str) -> str | None:
        if self.disabled:
            return None
        if len(text) > MAX_DETAIL_CHARS:
            text = text[:MAX_DETAIL_CHARS] + f"\n… ({len(text) - MAX_DETAIL_CHARS} more characters not shown)"
        try:
            path = self.detail_path(seq)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            return path.name
        except Exception:
            self.disabled = True
            logger.warning("activity log disabled for %s: a write failed", self.run_dir, exc_info=True)
            return None

    # --- reading ---------------------------------------------------------------------------

    def detail_path(self, seq: int) -> Path:
        return self.run_dir / DETAIL_DIR / f"{seq}.txt"

    def end_offset(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def read(self, offset: int = 0) -> tuple[list[dict], int]:
        """Complete lines from `offset` on, and the offset after the last complete one."""
        try:
            with self.path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return [], offset
        end = data.rfind(b"\n") + 1
        return _parse(data[:end]), offset + end

    def pending(self) -> dict | None:
        records, _ = self.read()
        ended = {r.get("seq") for r in records if r.get("phase") == "end"}
        for record in reversed(records):
            if record.get("phase") == "start" and record.get("seq") not in ended:
                return record
        return None

    def find(self, seq: int) -> dict | None:
        """The end record for `seq` (or its start record while it's still running)."""
        found = None
        for record in self.read()[0]:
            if record.get("seq") == seq:
                found = record
        return found


def activity_log(paths: ProjectPaths, run_id: str) -> ActivityLog:
    return ActivityLog(paths.run_dir(run_id))
