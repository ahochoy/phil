"""Records each agent tool call into the run's activity log (spec 2026-10-07, plan amendment A1:
a LangChain callback rather than middleware — callbacks reach every harness and every nested
sub-agent run without changing how agents are built)."""

import difflib
import logging
import threading
import time
from collections.abc import Iterable

from langchain_core.callbacks import BaseCallbackHandler

from phil.agents.collector import _tool_args
from phil.store.activity import ActivityLog

logger = logging.getLogger(__name__)

IGNORED_TOOLS = frozenset({"write_todos"})
_SHORT = 60  # a result is at most this many characters


def _first_line(text: str) -> str:
    for line in str(text).splitlines():
        if line.strip():
            return line.strip()[:_SHORT]
    return ""


def summarize_call(tool: str, args: dict | None) -> str:
    args = args or {}
    path = args.get("file_path") or args.get("path") or ""
    if tool == "read_file":
        return f"read {path}"
    if tool == "ls":
        return f"ls {path}".rstrip()
    if tool == "glob":
        return f"glob {args.get('pattern', '')}".rstrip()
    if tool == "grep":
        return f'grep "{args.get("pattern", "")}" {path}'.rstrip()
    if tool == "edit_file":
        return f"edit {path}"
    if tool == "write_file":
        return f"write {path}"
    if tool == "run_shell":
        return f"run {args.get('command', '')}".rstrip()
    if tool == "task":
        return f"sub-agent: {_first_line(args.get('description', ''))}".rstrip()
    return tool


def summarize_result(tool: str, args: dict | None, output: str) -> tuple[str, bool, str | None]:
    """(result, ok, detail). Reads, listings and searches have no detail."""
    args, output = args or {}, str(output)
    if tool == "run_shell":
        lines = output.splitlines()
        status = lines[0] if lines else ""
        ok = status.strip() == "exit_code: 0"
        body = [line for line in lines[1:] if not line.startswith("full log: ")]
        last = next((line.strip() for line in reversed(body) if line.strip()), status)
        return f"→ {last[:_SHORT]}", ok, output
    if tool == "edit_file":
        old, new = str(args.get("old_string", "")), str(args.get("new_string", ""))
        diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", lineterm=""))
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        return f"+{added} −{removed}", True, "\n".join(diff)
    if tool == "write_file":
        content = str(args.get("content", ""))
        return f"+{len(content.splitlines())}", True, content
    return "", True, None


class ActivityCallback(BaseCallbackHandler):
    """Writes a start record on `on_tool_start` and an end record on `on_tool_end`/`on_tool_error`,
    for every tool call in the agent's run tree. Never raises."""

    def __init__(self, log: ActivityLog, *, task: str | None, role: str, ignore_tools: Iterable[str] = ()) -> None:
        self._log, self._task, self._role = log, task, role
        self._ignore = IGNORED_TOOLS | set(ignore_tools)
        self._open: dict[object, tuple[int | None, float, str, dict | None, str]] = {}
        self._lock = threading.Lock()

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id, inputs=None, **kwargs) -> None:
        try:
            name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
            if name in self._ignore:
                return
            args = _tool_args(input_str, inputs)
            summary = summarize_call(name, args)
            seq = self._log.start(task=self._task, role=self._role, tool=name, summary=summary)
            with self._lock:
                self._open[run_id] = (seq, time.monotonic(), name, args, summary)
        except Exception:
            logger.debug("activity: on_tool_start failed", exc_info=True)

    def _finish(self, run_id, result: str, ok: bool, detail: str | None) -> None:
        with self._lock:
            opened = self._open.pop(run_id, None)
        if opened is None:
            return
        seq, started, name, _args, summary = opened
        self._log.end(seq, task=self._task, role=self._role, tool=name, summary=summary, result=result, ok=ok,
                      detail=detail, duration_ms=int((time.monotonic() - started) * 1000))

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        try:
            with self._lock:
                opened = self._open.get(run_id)
            if opened is None:
                return
            text = getattr(output, "content", output)
            result, ok, detail = summarize_result(opened[2], opened[3], text if isinstance(text, str) else str(text))
            self._finish(run_id, result, ok, detail)
        except Exception:
            logger.debug("activity: on_tool_end failed", exc_info=True)

    def on_tool_error(self, error: BaseException, *, run_id, **kwargs) -> None:
        try:
            self._finish(run_id, _first_line(str(error)) or type(error).__name__, False, str(error))
        except Exception:
            logger.debug("activity: on_tool_error failed", exc_info=True)
