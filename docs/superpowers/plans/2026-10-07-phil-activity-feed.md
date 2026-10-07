# Live Activity Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While a run works, the chat (and `phil attach`) shows a compact line per tool call grouped under each task, highlighted milestones, a live "now" row above the input, and `/more #n` details.

**Architecture:**
- **Recording.** A LangChain callback records every agent tool call into a per-run `activity.jsonl`, with optional detail files. The engine records its own test runs into the same log, and appends milestone events to the existing `events.jsonl`.
- **Following.** The chat's `RunWatcher` tails both files from byte offsets. A shared `FeedRenderer` turns records into Rich lines. A live row in the prompt shows the step in progress.

**Tech Stack:** Python 3.14, LangChain callbacks (`BaseCallbackHandler`), Rich, prompt_toolkit, pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-phil-activity-feed-design.md`. §6 lists the plan-time amendments.

**Base:** branch `plan-activity-feed`, which is stacked on `windows-port` (PR #29). Execute after PR #29 merges, then rebase onto `main`.

## Global Constraints

- **Editing and committing**
  - Edit files only with the Edit or Write tools. Never edit through python, perl, sed, heredocs or printf in Bash.
  - To commit, write the message to a file with Write, then run `git commit -F <file>`.
  - Every message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Safety**
  - Keep the words "keychain" and "credentials" out of Bash command lines.
  - Never work around a hook or guard; if one blocks you, report BLOCKED.
  - Never read or print `.env` files or key values.
- **Tests**
  - Never run `-m live` or `-m bench`.
  - Iterate with `uv run pytest <paths> -q -n 0`. Run the full `uv run pytest -q` once at the end of each task.
  - CI runs Ubuntu, macOS and Windows; keep tests portable (no POSIX-only commands in new tests).
- **Text I/O**
  - Every text read and write passes `encoding="utf-8"`.
  - Writes pass `newline="\n"`.
  - `tests/test_encoding.py` enforces both.
- **Recording must never fail a run or a tool call.** Every write in the activity path is guarded. The first failure logs one warning to the `phil` logger, and the log then disables itself.
- **Detail size limit:** each detail file is capped at `MAX_DETAIL_CHARS = 200_000` characters, keeping the head and appending `\n… (N more characters not shown)`.
- **Burst limit:** at most `BURST_LINES = 20` tool lines per render batch. The rest become one line: `… N more reads` if all the extra records are `read_file`, otherwise `… N more steps`.
- **Copy, verbatim:**
  - `#{seq} has no details.`
  - `No run to look in.`
  - `Usage: /more <n> or /more #<step>`

## Review Focus

1. **A tool call made by a sub-agent** (the deep agent's `task` tool) appears in the feed under the same task. LangChain callbacks propagate to child runs. Tested in Task 2 with a nested callback run id.
2. **A chat reopened mid-run** shows no replayed history. Its live row still shows the step already in progress. Tested in Task 4.
3. **A half-written final line** in `activity.jsonl`, from a worker killed mid-write, is not rendered, and is read once completed. Tested in Task 1.
4. **A feed line on a 40-column terminal** never wraps, and its `#n` survives. Tested in Task 3.
5. **`/more #n` for a seq without a detail file** (a read) prints `#{seq} has no details.` and never a traceback. Tested in Task 5.

---

### Task 1: The activity log store

**Files:**
- Create: `src/phil/store/activity.py`, `tests/store/test_activity.py`

**Interfaces:**
- Produces:
  - `ActivityLog(run_dir: Path)`
  - `.start(*, task: str | None, role: str, tool: str, summary: str) -> int | None`
  - `.end(seq: int, *, task, role, tool, summary, result: str, ok: bool, detail: str | None, duration_ms: int) -> None`
  - `.record(*, task, role, tool, summary, result, ok, detail, duration_ms) -> int | None`
  - `.read(offset: int = 0) -> tuple[list[dict], int]`
  - `.end_offset() -> int`
  - `.pending() -> dict | None`
  - `.find(seq: int) -> dict | None`
  - `.detail_path(seq: int) -> Path`
  - `.last_seq: int`
  - `activity_log(paths: ProjectPaths, run_id: str) -> ActivityLog`
  - `MAX_DETAIL_CHARS`
- **Record shapes:**
  - start: `{"seq", "ts", "phase": "start", "task", "role", "tool", "summary"}`
  - end: the same fields plus `"duration_ms", "result", "ok", "detail"`, where `detail` is the detail file's name or None, and `"phase": "end"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_activity.py
import json

from phil.store.activity import MAX_DETAIL_CHARS, ActivityLog


def test_start_and_end_append_records_with_one_seq(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.start(task="T1", role="implementer", tool="run_shell", summary="run pytest -q")
    log.end(seq, task="T1", role="implementer", tool="run_shell", summary="run pytest -q",
            result="→ 7 passed", ok=True, detail="7 passed in 0.1s", duration_ms=1200)
    records, offset = log.read()
    assert [r["phase"] for r in records] == ["start", "end"]
    assert records[0]["seq"] == records[1]["seq"] == seq == 1
    assert records[1]["result"] == "→ 7 passed" and records[1]["detail"] == "1.txt"
    assert log.detail_path(1).read_text(encoding="utf-8") == "7 passed in 0.1s"
    assert offset == log.end_offset()


def test_end_without_detail_writes_no_file(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.record(task="T1", role="implementer", tool="read_file", summary="read a.py",
                     result="", ok=True, detail=None, duration_ms=3)
    assert log.find(seq)["detail"] is None
    assert not log.detail_path(seq).exists()


def test_seq_is_seeded_from_an_existing_log(tmp_path):
    first = ActivityLog(tmp_path)
    first.record(task=None, role="engine", tool="gate", summary="gate pytest", result="", ok=True, detail=None, duration_ms=1)
    first.record(task=None, role="engine", tool="gate", summary="gate pytest", result="", ok=True, detail=None, duration_ms=1)
    assert ActivityLog(tmp_path).start(task=None, role="r", tool="t", summary="s") == 3


def test_a_half_written_line_is_not_read_until_complete(tmp_path):
    log = ActivityLog(tmp_path)
    log.record(task="T1", role="r", tool="t", summary="s", result="", ok=True, detail=None, duration_ms=1)
    with log.path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"seq": 9, "phase": "st')
    records, offset = log.read()
    assert [r["seq"] for r in records] == [1, 1]
    with log.path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('art", "summary": "x"}\n')
    more, _ = log.read(offset)
    assert more == [{"seq": 9, "phase": "start", "summary": "x"}]


def test_a_malformed_complete_line_is_skipped(tmp_path):
    log = ActivityLog(tmp_path)
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text("not json\n" + json.dumps({"seq": 1, "phase": "start"}) + "\n", encoding="utf-8")
    records, _ = log.read()
    assert records == [{"seq": 1, "phase": "start"}]


def test_pending_is_the_newest_start_without_an_end(tmp_path):
    log = ActivityLog(tmp_path)
    done = log.start(task="T1", role="r", tool="read_file", summary="read a")
    log.end(done, task="T1", role="r", tool="read_file", summary="read a", result="", ok=True, detail=None, duration_ms=1)
    log.start(task="T1", role="r", tool="run_shell", summary="run pytest")
    assert log.pending()["summary"] == "run pytest"


def test_detail_is_capped(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.record(task=None, role="r", tool="t", summary="s", result="", ok=True,
                     detail="x" * (MAX_DETAIL_CHARS + 10), duration_ms=1)
    text = log.detail_path(seq).read_text(encoding="utf-8")
    assert text.startswith("x" * MAX_DETAIL_CHARS) and text.endswith("(10 more characters not shown)")


def test_a_write_failure_disables_the_log_and_never_raises(tmp_path, monkeypatch, caplog):
    log = ActivityLog(tmp_path / "missing")
    monkeypatch.setattr(type(log.path), "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert log.start(task=None, role="r", tool="t", summary="s") is None
    assert log.start(task=None, role="r", tool="t", summary="s") is None
    assert sum("activity log disabled" in r.message for r in caplog.records) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/store/test_activity.py -q -n 0`
Expected: FAIL, because `phil.store.activity` doesn't exist.

- [ ] **Step 3: Implement**

```python
# src/phil/store/activity.py
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
```

**Ruling (an expected adjustment):** the write-failure test patches `Path.open` on the class. If that breaks pytest internals, patch `ActivityLog._append`'s target differently, for example by pointing `log.path` at a directory so `open("a")` raises `IsADirectoryError` or `PermissionError` on every OS. Keep the assertions: `None` both times, and one warning.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/store/test_activity.py tests/test_encoding.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Activity log: per-run tool-call records with detail files`, plus the trailer.

---

### Task 2: Recording agent tool calls, gate runs and milestones

**Files:**
- Create: `src/phil/agents/activity.py`, `tests/agents/test_activity_callback.py`, `tests/run/test_engine_activity.py`
- Modify:
  - `src/phil/agents/invoke.py`: the `AgentContext.activity` field; the callback is added in `invoke_agent`.
  - `src/phil/agents/fake.py`: a `fire_tool` helper for scripted turns.
  - `src/phil/run/engine.py`:
    - `RunDeps.activity`;
    - `_context` passes it;
    - `_test` records a gate line;
    - the check command records a gate line;
    - milestones are written in `pick_task`, `_verify`, `_failed_attempt`, `commit`, `_run_tester` and `_review`.
  - `src/phil/run/worker.py`: builds `activity_log(paths, run_id)` into `RunDeps`.
  - `src/phil/store/events.py`: `EventLog.read` skips malformed complete lines.

**Interfaces:**
- Consumes: `ActivityLog` (Task 1).
- Produces:
  - `phil.agents.activity.ActivityCallback(log: ActivityLog, *, task: str | None, role: str, ignore_tools: Iterable[str] = ())`
  - `summarize_call(tool: str, args: dict | None) -> str`
  - `summarize_result(tool: str, args: dict | None, output: str) -> tuple[str, bool, str | None]`, returning (result, ok, detail)
  - `IGNORED_TOOLS = frozenset({"write_todos"})`
  - `phil.agents.fake.fire_tool(turn: Turn, name: str, args: dict, output: str) -> None`
  - Milestone event kinds in `events.jsonl`:
    - `task_started {task, title, seq}`
    - `gate {task, name, seq}`, where name is `red`, `green` or `check` and it's written only when the gate passes
    - `attempt_failed {task, attempt, limit, problem, retrying, seq}`
    - `task_done {task, files, seq}`
    - `verdict {role, outcome, issues, blocking, seq}`, where outcome is `passed` or `changes` for the reviewer, and `passed` or `issues` for the tester
  - `MILESTONE_KINDS = ("task_started", "gate", "attempt_failed", "task_done", "verdict")`, in `phil.store.events`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_activity_callback.py
import uuid

from phil.agents.activity import ActivityCallback, summarize_call, summarize_result
from phil.store.activity import ActivityLog


def test_summaries_per_tool():
    assert summarize_call("read_file", {"file_path": "calc.py"}) == "read calc.py"
    assert summarize_call("edit_file", {"file_path": "calc.py", "old_string": "a", "new_string": "b"}) == "edit calc.py"
    assert summarize_call("run_shell", {"command": "pytest -q"}) == "run pytest -q"
    assert summarize_call("grep", {"pattern": "divide", "path": "src"}) == 'grep "divide" src'
    assert summarize_call("mystery", None) == "mystery"


def test_shell_result_takes_the_exit_code_and_last_line():
    output = "exit_code: 1\nfull log: /x/log\n..F\n1 failed, 7 passed in 0.31s\n"
    result, ok, detail = summarize_result("run_shell", {"command": "pytest -q"}, output)
    assert (result, ok) == ("→ 1 failed, 7 passed in 0.31s", False)
    assert "1 failed, 7 passed" in detail


def test_edit_result_counts_lines_and_keeps_a_diff():
    result, ok, detail = summarize_result(
        "edit_file", {"file_path": "calc.py", "old_string": "a\nb\n", "new_string": "a\nc\nd\n"}, "ok")
    assert (result, ok) == ("+2 −1", True)
    assert "-b" in detail and "+c" in detail


def test_reads_have_no_detail():
    assert summarize_result("read_file", {"file_path": "a.py"}, "1\tprint()") == ("", True, None)


def test_callback_records_start_and_end_including_a_nested_run(tmp_path):
    log = ActivityLog(tmp_path)
    callback = ActivityCallback(log, task="T1", role="implementer")
    parent, child = uuid.uuid4(), uuid.uuid4()
    callback.on_tool_start({"name": "task"}, "{}", run_id=parent, inputs={"description": "explore"})
    callback.on_tool_start({"name": "read_file"}, "", run_id=child, parent_run_id=parent, inputs={"file_path": "a.py"})
    callback.on_tool_end("contents", run_id=child, parent_run_id=parent)
    callback.on_tool_end("done", run_id=parent)
    records, _ = log.read()
    ends = [r for r in records if r["phase"] == "end"]
    assert [r["summary"] for r in ends] == ["read a.py", "sub-agent: explore"]
    assert all(r["task"] == "T1" and r["role"] == "implementer" for r in ends)


def test_callback_records_a_tool_error(tmp_path):
    log = ActivityLog(tmp_path)
    callback = ActivityCallback(log, task="T1", role="implementer")
    run = uuid.uuid4()
    callback.on_tool_start({"name": "edit_file"}, "", run_id=run, inputs={"file_path": "a.py"})
    callback.on_tool_error(ValueError("old_string not found\nmore"), run_id=run)
    end = log.read()[0][-1]
    assert (end["ok"], end["result"]) == (False, "old_string not found")


def test_ignored_tools_are_not_recorded(tmp_path):
    log = ActivityLog(tmp_path)
    callback = ActivityCallback(log, task=None, role="architect", ignore_tools={"PlanOutput"})
    for name in ("write_todos", "PlanOutput"):
        run = uuid.uuid4()
        callback.on_tool_start({"name": name}, "{}", run_id=run)
        callback.on_tool_end("x", run_id=run)
    assert log.read()[0] == []


def test_a_broken_log_never_breaks_the_callback(tmp_path):
    log = ActivityLog(tmp_path)
    log.disabled = True
    callback = ActivityCallback(log, task=None, role="r")
    run = uuid.uuid4()
    callback.on_tool_start({"name": "read_file"}, "", run_id=run, inputs={"file_path": "a"})
    callback.on_tool_end("x", run_id=run)  # no exception
```

```python
# tests/run/test_engine_activity.py
# Build a run exactly the way tests/run/conftest.py's existing engine tests do (read
# test_engine_escalation.py for the harness). The scripted implementer calls fire_tool for one
# edit and one shell command; the run then completes CALC-001 through red and green.
from phil.agents.fake import fire_tool
from phil.store.activity import activity_log
from phil.store.events import MILESTONE_KINDS


def test_a_run_records_tool_lines_gate_lines_and_milestones_in_order(make_harness):
    """Asserts:
    - activity.jsonl has end records for the scripted edit and run_shell (role implementer, task CALC-001)
      and 'gate' records (role engine, tool gate) for each _test call;
    - events.jsonl milestones, in order: task_started(CALC-001), gate(red), gate(green), task_done(CALC-001);
    - every milestone carries an int seq <= the activity log's last_seq at the end."""
    ...  # implementer fills in using the existing harness; see the Ruling below


def test_a_failed_attempt_writes_attempt_failed_with_retrying(make_harness):
    """A green attempt that fails the gate once, then passes: attempt_failed(attempt=1, retrying=True)
    appears before task_done."""
    ...
```

**Ruling (the engine tests):** the engine harness (`make_harness`, `calc_plan`, the scripted `write_red`/`write_green`/`bad_green` helpers in `tests/run/conftest.py`) is too large to restate here. The implementer writes these two tests with it. They must make the docstrings' assertions literally, and must not replace them with weaker checks.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_activity_callback.py tests/run/test_engine_activity.py -q -n 0`
Expected: FAIL, from import errors.

- [ ] **Step 3: Implement the callback**

```python
# src/phil/agents/activity.py
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
```

- [ ] **Step 4: Wire the callback into `invoke_agent`, and add `fire_tool`**

In `src/phil/agents/invoke.py`, add a field to `AgentContext`, after `prices`:

```python
    activity: "ActivityLog | None" = None  # the run's activity log: every tool call is recorded into it
```

Then import it under `TYPE_CHECKING`: `from phil.store.activity import ActivityLog`.

In `invoke_agent`, where `config={"callbacks": [collector], ...}` is built, change the callbacks to:

```python
        callbacks: list = [collector]
        if ctx.activity is not None:
            from phil.agents.activity import ActivityCallback

            callbacks.append(
                ActivityCallback(ctx.activity, task=task_id, role=spec.role, ignore_tools={spec.out_contract.__name__})
            )
```

Then pass `config={"callbacks": callbacks, "configurable": {TRACKER_KEY: tracker}}`.

In `src/phil/agents/fake.py`, add:

```python
def fire_tool(turn: "Turn", name: str, args: dict, output: str) -> None:
    """Make a scripted turn's tool call visible to the invoke config's callbacks, the way a real
    agent's tool call is (start, then end)."""
    import uuid

    run_id = uuid.uuid4()
    for callback in (turn.config or {}).get("callbacks", []):
        callback.on_tool_start({"name": name}, "", run_id=run_id, inputs=args)
        callback.on_tool_end(output, run_id=run_id)
```

- [ ] **Step 5: Wire the engine and worker**

In `src/phil/run/engine.py`:

- Add `activity: ActivityLog | None = None` to `RunDeps`, and pass `activity=self.deps.activity` in `_context`.
- Add a helper:

```python
    def _milestone(self, kind: str, **data: object) -> None:
        events = self.deps.events
        if events is None:
            return
        seq = self.deps.activity.last_seq if self.deps.activity is not None else 0
        try:
            events.append(kind, seq=seq, **data)
        except Exception:
            logger.warning("couldn't record the %s milestone", kind, exc_info=True)
```

  Add a module `logger = logging.getLogger(__name__)` if there isn't one.

- In `_test`, after `run_tests(...)` returns `report`, record a gate line (the role is `engine`):

```python
        activity = self.deps.activity
        if activity is not None:
            detail = None
            if report.log_path:
                try:
                    detail = Path(report.log_path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    detail = None
            counts = "passed" if report.passed else f"{len(report.failures)} failed"
            activity.record(task=self._current_task(state), role="engine", tool="gate",
                            summary=f"gate {report.command}", result=f"→ {counts}", ok=report.passed,
                            detail=detail, duration_ms=0)
```

  The current task id is read with:

```python
    @staticmethod
    def _current_task(state: RunState) -> str | None:
        index = state.get("task_index", -1)
        if index is None or index < 0:
            return None
        return load_plan(state).tasks[index].id
```

- In `_verify`'s check branch, after `run_check(...)` returns `check`, record a gate line the same way:

```python
            if self.deps.activity is not None:
                self.deps.activity.record(
                    task=task.id, role="engine", tool="gate", summary=f"gate {check.command}",
                    result=f"→ exit {check.exit_code}", ok=check.exit_code == 0,
                    detail=check.stdout + (f"\n{check.stderr}" if check.stderr else ""), duration_ms=check.duration_ms)
```

- **Milestones:**
  - `pick_task`, right after computing `index` when it isn't None: `self._milestone("task_started", task=plan.tasks[index].id, title=plan.tasks[index].description)`.
  - `_verify`:
    - before the red branch returns its success dict: `self._milestone("gate", task=task.id, name="red")`;
    - before each `green_ok` return: `self._milestone("gate", task=task.id, name="check" if task.verify == "check" else "green")`.
  - `_failed_attempt`, before returning `update`:

```python
        task = load_plan(state).tasks[state["task_index"]]
        self._milestone("attempt_failed", task=task.id, attempt=attempts, limit=self._attempt_limit(state),
                        problem=(problems[0] if problems else "the gate failed"),
                        retrying=update["verdict"] == "retry")
```

  - `commit`, after `self._update_run(...)`: `self._milestone("task_done", task=task.id, files=len(changed))`. To get `changed`, compute `changed = self.worktrees.changed_files(worktree, since=state["task_base_sha"])` **before** the commit at the top of `commit`. If `task_base_sha` is missing (a fix after review), use `self.worktrees.changed_files(worktree, since=self.worktrees.head(worktree))` instead, also before the commit.
  - `_run_tester`, before its return: `self._milestone("verdict", role="tester", outcome="issues" if issues else "passed", issues=len(issues), blocking=len(blocking))`.
  - `_review`, after `blocking` and `minor` are computed: `self._milestone("verdict", role="reviewer", outcome="passed" if verdict.verdict == "approve" else "changes", issues=len(verdict.issues), blocking=len(blocking))`. The reviewer's `verdict.verdict` is `"approve"` or `"changes"` (`contracts/results.py`).

In `src/phil/run/worker.py`, add `activity=activity_log(paths, run_id)` to `RunDeps(...)`, and import it from `phil.store.activity`.

In `src/phil/store/events.py`:
- add `MILESTONE_KINDS = ("task_started", "gate", "attempt_failed", "task_done", "verdict")`;
- make `read` skip malformed complete lines, the same way `_parse` does in Task 1: wrap `json.loads` in `try/except json.JSONDecodeError: continue`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/agents tests/run tests/store -q -n 0`
Expected: PASS. Existing engine tests must pass unchanged. A test that asserts the exact contents of `events.jsonl` may need the new milestone kinds allowed for; adapt it only by filtering out `MILESTONE_KINDS`, never by dropping its own assertions.

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Record agent tool calls, gate runs and task milestones for the activity feed`, plus the trailer.

---

### Task 3: The feed renderer and the live row

**Files:**
- Create: `src/phil/ui/feed_view.py`, `tests/ui/test_feed_view.py`
- Modify:
  - `src/phil/ui/theme.py`: band styles.
  - `src/phil/chat/state.py`: `LiveStep`, `ToolbarView.live`, `set_live`.
  - `src/phil/ui/toolbar.py`: `render_live_row`.
  - `tests/ui/test_toolbar.py`: or wherever the toolbar is tested; find it.

**Interfaces:**
- Consumes: the record and milestone shapes (Tasks 1–2).
- Produces:
  - `FeedRenderer()`, with:
    - `.tool_lines(records: list[dict], width: int) -> list[Text]`, which uses end records only;
    - `.milestone(event: dict, width: int) -> Text`.
  - `BURST_LINES = 20`
  - `LiveStep(task: str | None, role: str, summary: str, started: float)`, a frozen dataclass in `phil.chat.state`
  - `ToolbarView.live: LiveStep | None = None`
  - `ChatState.set_live(live: LiveStep | None)`
  - `render_live_row(view: ToolbarView, now: float, width: int | None = None) -> str`, which returns `""` with no active run
  - `NODE_LABELS: dict[str, str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_feed_view.py
from phil.ui.feed_view import BURST_LINES, FeedRenderer


def end(seq, tool, summary, result="", detail=None, task="CALC-001", role="implementer", ms=10):
    return {"seq": seq, "phase": "end", "task": task, "role": role, "tool": tool, "summary": summary,
            "result": result, "ok": True, "detail": detail, "duration_ms": ms}


def plain(lines):
    return [line.plain for line in lines]


def test_tool_lines_are_indented_with_verb_result_duration_and_ref():
    lines = FeedRenderer().tool_lines([
        end(13, "edit_file", "edit calc.py", "+4 −1", "13.txt"),
        end(14, "run_shell", "run pytest -q", "→ 2 failed", "14.txt", ms=1800),
    ], width=120)
    assert plain(lines) == ["    edit  calc.py  +4 −1  #13", "    run   pytest -q  → 2 failed · 1.8s  #14"]


def test_consecutive_reads_fold_into_one_line():
    lines = FeedRenderer().tool_lines([end(1, "read_file", "read calc.py"), end(2, "read_file", "read tests/t.py")], 120)
    assert plain(lines) == ["    read  calc.py · tests/t.py"]


def test_reads_in_different_tasks_do_not_fold():
    lines = FeedRenderer().tool_lines([end(1, "read_file", "read a.py"), end(2, "read_file", "read b.py", task="T2")], 120)
    assert len(lines) == 2


def test_start_records_are_ignored():
    assert FeedRenderer().tool_lines([{"seq": 1, "phase": "start", "summary": "run x"}], 120) == []


def test_a_narrow_line_never_wraps_and_keeps_its_ref():
    long = "run " + "x" * 200
    (line,) = FeedRenderer().tool_lines([end(7, "run_shell", long, "→ 1 failed", "7.txt", ms=900)], width=40)
    assert len(line.plain) <= 39 and line.plain.endswith("#7") and "…" in line.plain


def test_a_burst_collapses_past_the_limit():
    records = [end(i, "edit_file", f"edit f{i}.py", "+1 −0", f"{i}.txt") for i in range(1, BURST_LINES + 8)]
    lines = plain(FeedRenderer().tool_lines(records, 120))
    assert len(lines) == BURST_LINES + 1 and lines[-1] == "    … 7 more steps"


def test_a_burst_of_reads_says_reads():
    records = [end(i, "grep", f'grep "x" f{i}', task=f"T{i}") for i in range(1, BURST_LINES + 4)]
    assert plain(FeedRenderer().tool_lines(records, 120))[-1] == "    … 3 more steps"


def test_milestones():
    feed = FeedRenderer()
    assert feed.milestone({"kind": "task_started", "task": "CALC-001", "title": "Add multiply", "ts": "2026-10-07T10:00:00Z"}, 120).plain == "▸ CALC-001 Add multiply"
    assert feed.milestone({"kind": "gate", "task": "CALC-001", "name": "red"}, 120).plain == "✓ CALC-001 red: the new tests fail as expected"
    assert feed.milestone({"kind": "gate", "task": "CALC-001", "name": "green"}, 120).plain == "✓ CALC-001 green: tests pass"
    assert feed.milestone({"kind": "attempt_failed", "task": "CALC-001", "attempt": 1, "limit": 3, "problem": "2 tests failed", "retrying": True}, 120).plain == "✗ CALC-001 attempt 1 failed: 2 tests failed · retrying (2 of 3)"
    assert feed.milestone({"kind": "attempt_failed", "task": "CALC-001", "attempt": 3, "limit": 3, "problem": "x", "retrying": False}, 120).plain == "✗ CALC-001 attempt 3 failed: x · needs you"
    assert feed.milestone({"kind": "task_done", "task": "CALC-001", "files": 2, "ts": "2026-10-07T10:00:41Z"}, 120).plain == "✓ CALC-001 done · 2 files · 41s"
    assert feed.milestone({"kind": "verdict", "role": "reviewer", "outcome": "passed", "issues": 0, "blocking": 0}, 120).plain == "✓ reviewer approved"
    assert feed.milestone({"kind": "verdict", "role": "reviewer", "outcome": "changes", "issues": 3, "blocking": 1}, 120).plain == "✗ reviewer asked for changes: 3 issues (1 blocking)"
    assert feed.milestone({"kind": "verdict", "role": "tester", "outcome": "passed", "issues": 0, "blocking": 0}, 120).plain == "✓ tester: no issues"


def test_task_done_without_a_known_start_omits_the_elapsed_time():
    assert FeedRenderer().milestone({"kind": "task_done", "task": "T9", "files": 1, "ts": "2026-10-07T10:00:00Z"}, 120).plain == "✓ T9 done · 1 file"
```

```python
# add to the toolbar tests (find the existing file with `grep -rl render_toolbar tests`)
from phil.chat.state import LiveStep, RunView, ToolbarView
from phil.ui.toolbar import render_live_row


def test_live_row_shows_the_running_tool():
    run = RunView(run_id="r-1", keyword="calc", node="implement", tasks_done=0, tasks_total=2, started=0.0)
    view = ToolbarView(run=run, live=LiveStep(task="CALC-002", role="reviewer", summary="read README.md", started=100.0))
    assert render_live_row(view, now=108.0)[2:] == "CALC-002 · reviewer · read README.md · 8s"


def test_live_row_falls_back_to_the_stage_and_is_empty_without_a_run():
    run = RunView(run_id="r-1", keyword="calc", node="pick_task", tasks_done=0, tasks_total=2, started=0.0)
    assert render_live_row(ToolbarView(run=run), now=5.0)[2:] == "Picking the next task"
    assert render_live_row(ToolbarView(), now=5.0) == ""


def test_live_row_fits_the_width():
    run = RunView(run_id="r-1", keyword="calc", node="implement", tasks_done=0, tasks_total=2, started=0.0)
    view = ToolbarView(run=run, live=LiveStep(task="T1", role="implementer", summary="run " + "x" * 300, started=0.0))
    assert len(render_live_row(view, now=1.0, width=40)) <= 39
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/ui -q -n 0`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/phil/ui/theme.py`, add these styles to `PHIL_THEME`:

```python
        "phil.band.start": "bold cyan on grey15",
        "phil.band.pass": "green on grey15",
        "phil.band.fail": "red on grey15",
        "phil.band.wait": "yellow on grey15",
        "phil.ref": "blue",
```

```python
# src/phil/ui/feed_view.py
"""The activity feed's lines (spec 2026-10-07 §3.5): compact tool lines under each task and
highlighted milestone bands. Shared by the chat and `phil attach`."""

from datetime import datetime

from rich.cells import cell_len
from rich.text import Text

from phil.ui.toolbar import elapsed

BURST_LINES = 20
INDENT = "    "
_READS = ("read_file",)
_TIMED = ("run_shell", "gate")


def _seconds(ts: str | None) -> float | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _cut(text: str, cells: int) -> str:
    if cells <= 1:
        return "…" if cells == 1 else ""
    if cell_len(text) <= cells:
        return text
    out = ""
    for char in text:
        if cell_len(out + char) > cells - 1:
            break
        out += char
    return out + "…"


class FeedRenderer:
    def __init__(self) -> None:
        self._task_started: dict[str, float] = {}

    def _line(self, record: dict, width: int, summary: str | None = None) -> Text:
        summary = summary if summary is not None else str(record.get("summary", ""))
        verb, _, rest = summary.partition(" ")
        verb = "gate" if record.get("tool") == "gate" else verb
        tail = ""
        if record.get("result"):
            tail += f"  {record['result']}"
        if record.get("tool") in _TIMED and record.get("duration_ms"):
            tail += f" · {record['duration_ms'] / 1000:.1f}s"
        ref = f"  #{record['seq']}" if record.get("detail") else ""
        head = f"{INDENT}{verb:<5} "
        budget = width - 1 - cell_len(head) - cell_len(ref)
        if cell_len(rest) + cell_len(tail) > budget and " · " in tail:
            tail = tail.split(" · ")[0]  # narrow: drop the duration first
        rest = _cut(rest, max(budget - cell_len(tail), 1))
        line = Text(f"{head}{rest}{tail}", style="phil.muted")
        if ref:
            line.append(ref, style="phil.ref")
        return line

    def tool_lines(self, records: list[dict], width: int) -> list[Text]:
        ends = [r for r in records if r.get("phase") == "end"]
        groups: list[list[dict]] = []
        for record in ends:
            last = groups[-1] if groups else None
            if (last and record.get("tool") in _READS and last[-1].get("tool") in _READS
                    and last[-1].get("task") == record.get("task") and last[-1].get("role") == record.get("role")):
                last.append(record)
            else:
                groups.append([record])
        lines: list[Text] = []
        for group in groups[:BURST_LINES]:
            if len(group) > 1:
                paths = " · ".join(str(r.get("summary", "")).partition(" ")[2] for r in group)
                lines.append(self._line(group[-1], width, summary=f"read {paths}"))
            else:
                lines.append(self._line(group[0], width))
        extra = groups[BURST_LINES:]
        if extra:
            count = sum(len(g) for g in extra)
            noun = "reads" if all(r.get("tool") in _READS for g in extra for r in g) else "steps"
            lines.append(Text(f"{INDENT}… {count} more {noun}", style="phil.muted"))
        return lines

    def milestone(self, event: dict, width: int) -> Text:
        kind, task = event.get("kind"), event.get("task", "")
        if kind == "task_started":
            started = _seconds(event.get("ts"))
            if started is not None:
                self._task_started[task] = started
            return Text(f"▸ {task} {event.get('title', '')}".rstrip(), style="phil.band.start")
        if kind == "gate":
            words = {"red": "red: the new tests fail as expected", "green": "green: tests pass",
                     "check": "check passed"}.get(event.get("name"), f"{event.get('name')} passed")
            return Text(f"✓ {task} {words}", style="phil.band.pass")
        if kind == "attempt_failed":
            attempt, limit = event.get("attempt", 1), event.get("limit", 1)
            then = f"retrying ({attempt + 1} of {limit})" if event.get("retrying") else "needs you"
            return Text(f"✗ {task} attempt {attempt} failed: {event.get('problem', '')} · {then}", style="phil.band.fail")
        if kind == "task_done":
            files = event.get("files", 0)
            text = f"✓ {task} done · {files} file{'s' if files != 1 else ''}"
            started, ended = self._task_started.pop(task, None), _seconds(event.get("ts"))
            if started is not None and ended is not None:
                text += f" · {elapsed(ended - started)}"
            return Text(text, style="phil.band.pass")
        if kind == "verdict":
            role, issues, blocking = event.get("role"), event.get("issues", 0), event.get("blocking", 0)
            if event.get("outcome") == "passed":
                return Text("✓ reviewer approved" if role == "reviewer" else f"✓ {role}: no issues", style="phil.band.pass")
            if role == "reviewer":
                return Text(f"✗ reviewer asked for changes: {issues} issues ({blocking} blocking)", style="phil.band.fail")
            return Text(f"✗ {role}: {issues} issues ({blocking} blocking)", style="phil.band.fail")
        return Text(str(kind), style="phil.muted")
```

Note that `tests/ui/test_feed_view.py::test_tool_lines_are_indented_with_verb_result_duration_and_ref` expects `edit  calc.py  +4 −1  #13`. That's the verb padded to 5 cells, then a space, then the rest. Adjust `head`'s padding if the test and the code disagree by a space. The test's exact strings are the contract.

In `src/phil/chat/state.py`:

```python
@dataclass(frozen=True)
class LiveStep:
    task: str | None
    role: str
    summary: str
    started: float  # epoch seconds
```

Add `live: LiveStep | None = None` to `ToolbarView`, and add `def set_live(self, live: LiveStep | None) -> None: self._update(live=live)` to the state class next to `set_run`.

In `src/phil/ui/toolbar.py`:

```python
NODE_LABELS = {
    "setup": "Setting up", "pick_task": "Picking the next task", "implement": "Implementing",
    "verify": "Verifying", "commit": "Committing", "tester": "Testing", "tester_task": "Testing",
    "review": "Reviewing", "finish": "Finishing", "escalate": "Waiting for you",
}


def render_live_row(view: ToolbarView, now: float, width: int | None = None) -> str:
    """The live row above the input: the running tool, else the run's stage; empty without a run."""
    if view.run is None:
        return ""
    if view.live is not None:
        live = view.live
        frame = SPINNER[int((now - live.started) * 8) % len(SPINNER)]
        parts = [p for p in (live.task, live.role, live.summary, elapsed(now - live.started)) if p]
        text = f"{frame} " + " · ".join(parts)
    else:
        node = view.run.node or "starting"
        text = "  " + NODE_LABELS.get(node, node)
    return _fit(text, width)
```

`_fit` is the existing helper that `render_toolbar` uses. Use it so the width rule is shared.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ui -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Activity feed renderer and live row`, plus the trailer.

---

### Task 4: Following the feed in the chat

**Files:**
- Modify:
  - `src/phil/chat/watcher.py`: offsets, the `activity`, `milestone` and `live_step` events.
  - `src/phil/chat/controller.py`:
    - the `_on_activity`, `_on_milestone` and `_on_live_step` handlers;
    - a `FeedRenderer` per chat;
    - the live step cleared on `run_done`;
    - `"activity"`, `"milestone"` and `"live_step"` added to the tuple of run event kinds the controller accepts (around line 102).
  - `src/phil/chat/terminal.py`: `TerminalIO(toolbar, live_row=None)`, with the prompt message as a callable.
  - `src/phil/cli/main.py`: `_terminal(toolbar, live_row)` and the `live_row` closure.
- Test:
  - `tests/chat/test_watcher.py`, or the existing watcher test file (find it with `grep -rl RunWatcher tests`);
  - `tests/chat/test_controller_feed.py`;
  - `tests/chat/test_terminal.py`, or the existing one.

**Interfaces:**
- Consumes:
  - `activity_log`, `MILESTONE_KINDS`
  - `FeedRenderer`
  - `LiveStep`
  - `ChatState.set_live`
  - `render_live_row`
- Produces:
  - Watcher posts:
    - `ChatEvent("activity", {"records": list[dict]})`, at most one per poll;
    - `ChatEvent("milestone", <event dict>)`, one per milestone;
    - `ChatEvent("live_step", {"task", "role", "summary", "started"} | {})`, when it changes, with an empty dict meaning none.

- [ ] **Step 1: Write the failing tests**

```python
# watcher tests: add to the existing RunWatcher test module; reuse its fixtures (a run row, ProjectPaths).
def test_watcher_starts_at_the_end_and_posts_only_new_activity(watcher_setup):
    """Given activity.jsonl and events.jsonl with old records before the watcher is created:
    - the first poll posts no 'activity' and no 'milestone' events;
    - after appending one end record and one task_started milestone, the next poll posts exactly one
      'activity' event whose records are the new start+end, and one 'milestone' event."""


def test_watcher_seeds_the_live_step_from_a_running_tool(watcher_setup):
    """A start record without an end exists before the watcher starts: the first poll posts
    live_step with that record's summary; after its end record is appended, the next poll posts live_step {}."""


def test_watcher_skips_a_half_written_activity_line(watcher_setup):
    """A trailing partial line is not posted; once completed it is."""
```

**Ruling:** the watcher tests above are specified by their docstrings. Write them against the existing watcher test fixtures, which can post into a list. Every docstring assertion must be made.

```python
# tests/chat/test_controller_feed.py — use the controller test harness the other chat tests use
# (read tests/chat/test_controller*.py for how they build a Controller with a recording console).
def test_activity_and_milestones_print_feed_lines(controller_with_run):
    """controller.handle_event(ChatEvent('milestone', {'kind': 'task_started', 'task': 'CALC-001', 'title': 'Add multiply', 'ts': ...}))
    then handle_event(ChatEvent('activity', {'records': [<end record run_shell 'run pytest -q' '→ 7 passed' detail '3.txt'>]}))
    -> the console's exported text contains '▸ CALC-001 Add multiply' then 'run   pytest -q  → 7 passed' and '#3'."""


def test_live_step_sets_and_clears_the_live_row(controller_with_run):
    """handle_event(ChatEvent('live_step', {'task': 'T1', 'role': 'implementer', 'summary': 'run pytest', 'started': 1.0}))
    -> controller.state.view().live == LiveStep('T1', 'implementer', 'run pytest', 1.0);
    handle_event(ChatEvent('live_step', {})) -> live is None; a run_done event also clears it."""
```

```python
# terminal test: the prompt message includes the live row when there is one
def test_prompt_message_puts_the_live_row_above_the_input():
    from prompt_toolkit.formatted_text import to_plain_text
    from phil.chat.terminal import TerminalIO
    io = TerminalIO(lambda: "", live_row=lambda: "⠋ T1 · implementer · run pytest · 3s", input=..., output=...)
    # use the same create_pipe_input/DummyOutput fixtures the existing terminal tests use
    assert to_plain_text(io._message("you › ")()) == "⠋ T1 · implementer · run pytest · 3s\nyou › "
    io2 = TerminalIO(lambda: "", live_row=lambda: "", input=..., output=...)
    assert to_plain_text(io2._message("you › ")()) == "you › "
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/chat -q -n 0`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement the watcher**

In `RunWatcher.__init__`:

```python
        self.activity = activity_log(paths, run_id)
        self._event_offset = self._size(self.events.path)
        self._activity_offset = self.activity.end_offset()
        pending = self.activity.pending()
        self._live: dict = _live_of(pending) if pending else {}
        self._live_posted: dict | None = None
```

Add these helpers at module level:

```python
def _size(path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _live_of(record: dict) -> dict:
    started = datetime.fromisoformat(str(record.get("ts", "")).replace("Z", "+00:00")).timestamp() if record.get("ts") else time.time()
    return {"task": record.get("task"), "role": record.get("role"), "summary": record.get("summary", ""), "started": started}
```

Make `_size` a `staticmethod` on the class, or call the module function; either is fine. At the start of `poll_once`'s `try`, before the run-row snapshot, add:

```python
            new_events, self._event_offset = self.events.read(self._event_offset)
            for event in new_events:
                if event.get("kind") in MILESTONE_KINDS:
                    self.post(ChatEvent("milestone", event))
            records, self._activity_offset = self.activity.read(self._activity_offset)
            if records:
                self.post(ChatEvent("activity", {"records": records}))
                open_starts = {r["seq"]: r for r in records if r.get("phase") == "start"}
                for record in records:
                    if record.get("phase") == "end":
                        open_starts.pop(record.get("seq"), None)
                        if self._live and record.get("summary") == self._live.get("summary"):
                            self._live = {}
                if open_starts:
                    self._live = _live_of(open_starts[max(open_starts)])
            if self._live != self._live_posted:
                self._live_posted = dict(self._live)
                self.post(ChatEvent("live_step", dict(self._live)))
```

Note that `EventLog.read(offset)` returns `(events, new_offset)`; Task 2 made it tolerant of malformed lines.

- [ ] **Step 4: Implement the controller and terminal changes**

In the controller:
- Construct `self._feed = FeedRenderer()` in `__init__`.
- Add the three kinds to the accepted run-event tuple.
- Add the handlers:

```python
    def _on_activity(self, data: dict) -> None:
        for line in self._feed.tool_lines(data.get("records", []), self.console.width):
            self.console.print(line, soft_wrap=True)

    def _on_milestone(self, data: dict) -> None:
        self.console.print(self._feed.milestone(data, self.console.width))

    def _on_live_step(self, data: dict) -> None:
        if not data:
            self.state.set_live(None)
            return
        self.state.set_live(LiveStep(task=data.get("task"), role=str(data.get("role", "")),
                                     summary=str(data.get("summary", "")), started=float(data.get("started", time.time()))))
```

At the top of `_on_run_done`, add `self.state.set_live(None)`.

In `TerminalIO`:
- add a `live_row: Callable[[], str] | None = None` parameter, stored as `self._live_row`;
- add the method below;
- in `ask`, pass `self._message(prompt)` instead of `FormattedText([("bold", prompt)])`.

```python
    def _message(self, prompt: str) -> Callable[[], FormattedText]:
        def message() -> FormattedText:
            try:
                row = self._live_row() if self._live_row else ""
            except Exception:  # a redraw must never take the prompt down
                row = ""
            parts = [("class:live", row + "\n")] if row else []
            return FormattedText([*parts, ("bold", prompt)])

        return message
```

In `cli/main.py`:
- change `_terminal(toolbar)` to `_terminal(toolbar, live_row=None)`, returning `TerminalIO(toolbar, live_row=live_row)`;
- next to the `toolbar` closure, add:

```python
    def live_row() -> str:
        if controller is None:
            return ""
        return render_live_row(controller.state.view(), time.time(), width=terminal.width())
```

  Mirror exactly how the `toolbar` closure guards `controller`, and pass it with `_terminal(toolbar, live_row)`. Tests that patch `_terminal` with a one-argument fake must accept the new keyword: update those fakes to `lambda toolbar, live_row=None: ...`.

- [ ] **Step 5: An end-to-end check with a scripted agent**

Add to `tests/chat/test_controller_feed.py` a test that follows a real scripted run, the way the existing chat tests follow one with `RunWatcher.poll_once`. The scripted implementer calls `fire_tool(turn, "run_shell", {"command": "pytest -q"}, "exit_code: 0\n7 passed")`. The test then asserts:
- the chat's console shows `▸ CALC-001`;
- it shows `run   pytest -q  → 7 passed`;
- it shows a `✓ CALC-001 done` band.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/chat tests/cli tests/ui -q -n 0`
Expected: PASS.

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Follow the activity feed in the chat: tool lines, milestones and the live row`, plus the trailer.

---

### Task 5: Details on demand, `phil show --step`, and `phil attach`

**Files:**
- Modify:
  - `src/phil/chat/controller.py`: `_more_command` handles `#n`, and the old message is changed to `No detail {n}.`
  - `src/phil/cli/main.py`: `show_command` gets `--step`.
  - `src/phil/cli/attach.py`: feed lines and milestones.
  - The help text: mention `/more #n` in `HELP`.
- Test: `tests/chat/test_controller_feed.py`, `tests/cli/test_show_command.py` or the existing show tests, `tests/cli/test_attach.py`.

**Interfaces:**
- Consumes: `activity_log`, `ActivityLog.find/detail_path`, `detail_text`, `FeedRenderer`, `MILESTONE_KINDS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/chat/test_controller_feed.py (continued)
def test_more_hash_prints_the_detail_in_a_panel(controller_with_run):
    """With the chat's run having activity seq 14 (end record summary 'run pytest -q', detail '14.txt'
    containing '2 failed'), `/more #14` prints a panel titled 'run pytest -q' containing '2 failed'."""


def test_more_hash_without_detail(controller_with_run):
    """A read (no detail file): `/more #3` prints exactly '#3 has no details.'"""


def test_more_hash_without_a_run(controller_without_run):
    """`/more #3` with no run prints 'No run to look in.'"""


def test_more_plain_number_keeps_its_meaning(controller_with_run):
    """`/more 2` with no refs listed prints 'No detail 2. Use /show to list them.'; `/more x` prints the usage line
    'Usage: /more <n> or /more #<step>'."""
```

```python
# CLI
def test_show_step_prints_an_activity_detail(...):
    """`phil show <run> --step 14` prints the detail file's text; an unknown step exits 1 with '#14 has no details.'"""


def test_attach_prints_milestones_and_new_tool_lines(...):
    """attach() over a run whose events.jsonl has task_started and whose activity grows during the poll:
    the console shows the '▸ CALC-001' band and the new tool line. Activity written before attach
    started is not printed (plan amendment A5)."""
```

**Ruling:** write these tests with the existing chat, CLI and attach harnesses. Every docstring assertion must be made.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/chat/test_controller_feed.py tests/cli -q -n 0`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

In `_more_command`, at the start:

```python
        if arg.startswith("#") and arg[1:].isdigit():
            self._more_step(int(arg[1:]))
            return
        if not arg.isdigit():
            self.console.print("Usage: /more <n> or /more #<step>")
            return
```

Change `f"No detail #{n}. Use /show to list them."` to `f"No detail {n}. Use /show to list them."`, and add:

```python
    def _more_step(self, seq: int) -> None:
        if not self._run_id:
            self.console.print("No run to look in.")
            return
        log = activity_log(self.paths, self._run_id)
        record = log.find(seq)
        path = log.detail_path(seq)
        if record is None or not record.get("detail") or not path.exists():
            self.console.print(f"#{seq} has no details.")
            return
        try:
            text = detail_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            self.console.print(f"[phil.error]Couldn't read {escape(str(path))}: {escape(type(exc).__name__)}[/]")
            return
        self.console.print(Panel(Text(text), title=Text(str(record.get("summary", f"#{seq}"))), title_align="left"))
```

Use the controller's existing attributes for the project paths and the current run id. Check the names: `self.paths` / `self._run_id`, or whatever `_notice_refs` uses.

In `show_command`, add an option `step: int | None = typer.Option(None, "--step", help="Print activity step #N's detail.")`. When it's set:

```python
    if step is not None:
        log = activity_log(paths, run_id)
        record, path = log.find(step), log.detail_path(step)
        if record is None or not record.get("detail") or not path.exists():
            console.print(f"[phil.error]#{step} has no details.[/]")
            raise typer.Exit(1)
        typer.echo(show_view.detail_text(path))
        return
```

Put this before the `n is None` branch.

In `attach.py`:
- `render_event(console, event, run_id="", feed: FeedRenderer | None = None)`. When `event["kind"] in MILESTONE_KINDS`, print `(feed or FeedRenderer()).milestone(event, console.width)`.
- In `attach()`:
  - create `feed = FeedRenderer()`, `activity = ActivityLog(events.path.parent)` and `activity_offset = activity.end_offset()`;
  - pass `feed` to `render_event`;
  - on each loop, after the events: `records, activity_offset = activity.read(activity_offset)`, then print each line from `feed.tool_lines(records, console.width)`.

Extend `HELP`'s command list with `/more #<step> (a feed step's detail)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat tests/cli -q -n 0`
Expected: PASS.

- [ ] **Step 5: README and roadmap**

- **README:** add a short "Following a run" note in its chat section. It covers:
  - the feed (tool lines, milestone bands);
  - the live row;
  - `/more #n`;
  - `phil show <run> --step N`.
- **`docs/superpowers/roadmap.md`, M5:** mark the live event stream and the activity feed as done, linking the spec and this plan.

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Activity details on demand: /more #n, phil show --step, and the feed in phil attach`, plus the trailer.
