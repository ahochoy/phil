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
SUB_TOOL = "task"  # deepagents' tool that runs a sub-agent
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
    for every tool call in the agent's run tree. Never raises.

    A call made inside a sub-agent (a `task` call) is tagged with that task call's `sub_id` (its
    seq) and `sub` (its description). To find it, the callback remembers the parent of every run it
    sees (chains, models and tools), and walks up from a tool call to the nearest open task call."""

    def __init__(self, log: ActivityLog, *, task: str | None, role: str, ignore_tools: Iterable[str] = ()) -> None:
        self._log, self._task, self._role = log, task, role
        self._ignore = IGNORED_TOOLS | set(ignore_tools)
        self._open: dict[object, tuple[int | None, float, str, dict | None, str, dict | None]] = {}
        self._parents: dict[object, object] = {}  # LangChain run id -> parent run id
        self._subs: dict[object, tuple[int | None, str]] = {}  # an open task call's run id -> (seq, description)
        self._lock = threading.Lock()

    # --- the run tree ----------------------------------------------------------------------

    def _note_parent(self, run_id, parent_run_id) -> None:
        try:
            if run_id is not None and parent_run_id is not None:
                with self._lock:
                    self._parents[run_id] = parent_run_id
        except Exception:
            logger.debug("activity: parent bookkeeping failed", exc_info=True)

    def on_chain_start(self, serialized, inputs, *, run_id=None, parent_run_id=None, **kwargs) -> None:
        self._note_parent(run_id, parent_run_id)

    def on_chat_model_start(self, serialized, messages, *, run_id=None, parent_run_id=None, **kwargs) -> None:
        self._note_parent(run_id, parent_run_id)

    def on_llm_start(self, serialized, prompts, *, run_id=None, parent_run_id=None, **kwargs) -> None:
        self._note_parent(run_id, parent_run_id)

    def _sub_for(self, parent_run_id) -> dict:
        """The nearest open task call above `parent_run_id`, as record fields; {} if none."""
        seen, node = set(), parent_run_id
        with self._lock:
            while node is not None and node not in seen:
                if node in self._subs:
                    seq, description = self._subs[node]
                    return {"sub_id": seq, "sub": description} if seq is not None else {}
                seen.add(node)
                node = self._parents.get(node)
        return {}

    def reset(self) -> None:
        """Forget the run tree (call when the agent call finishes)."""
        with self._lock:
            self._parents.clear()
            self._subs.clear()

    # --- tool calls ------------------------------------------------------------------------

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id, parent_run_id=None, inputs=None,
                      **kwargs) -> None:
        try:
            self._note_parent(run_id, parent_run_id)
            name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
            if name in self._ignore:
                return
            args = _tool_args(input_str, inputs)
            summary = summarize_call(name, args)
            extra = self._sub_for(parent_run_id) or None  # a task call is never inside itself
            seq = self._log.start(task=self._task, role=self._role, tool=name, summary=summary, extra=extra)
            with self._lock:
                self._open[run_id] = (seq, time.monotonic(), name, args, summary, extra)
                if name == SUB_TOOL:
                    self._subs[run_id] = (seq, _first_line(args.get("description", "")) if args else "")
        except Exception:
            logger.debug("activity: on_tool_start failed", exc_info=True)

    def _finish(self, run_id, result: str, ok: bool, detail: str | None) -> None:
        with self._lock:
            opened = self._open.pop(run_id, None)
            self._subs.pop(run_id, None)
            self._parents.pop(run_id, None)
        if opened is None:
            return
        seq, started, name, _args, summary, extra = opened
        self._log.end(seq, task=self._task, role=self._role, tool=name, summary=summary, result=result, ok=ok,
                      detail=detail, duration_ms=int((time.monotonic() - started) * 1000), extra=extra)

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        try:
            with self._lock:
                opened = self._open.get(run_id)
            if opened is None:
                return
            text = getattr(output, "content", output)
            text = text if isinstance(text, str) else str(text)
            if getattr(output, "status", None) == "error":
                # deepagents' file tools report a failure as a ToolMessage(status="error"), not by raising.
                self._finish(run_id, _first_line(text) or "error", False, text)
                return
            result, ok, detail = summarize_result(opened[2], opened[3], text)
            self._finish(run_id, result, ok, detail)
        except Exception:
            logger.debug("activity: on_tool_end failed", exc_info=True)

    def on_tool_error(self, error: BaseException, *, run_id, **kwargs) -> None:
        try:
            self._finish(run_id, _first_line(str(error)) or type(error).__name__, False, str(error))
        except Exception:
            logger.debug("activity: on_tool_error failed", exc_info=True)
