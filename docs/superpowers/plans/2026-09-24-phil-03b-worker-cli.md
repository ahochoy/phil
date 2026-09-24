# Phil Plan 3b: Worker and Run CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the plan-3a engine in a detached worker process and give the user the commands to live with it: `phil run plan.json`, `phil attach`, `phil resume`, `phil stop`, `phil diff`, `phil clean`, with a heartbeat, crash detection, a validated run-state machine, an event log for progress, and clean shutdown of child processes.

**Architecture:** One worker process per stretch of a run (`python -m phil _worker <run-id> --mode start|resume|continue`): it drives the graph until the run pauses, finishes, is stopped, or crashes, then exits. The worker, not the engine, marks a run `escalated` and records the escalation in an append-only event log (`runs/<id>/events.jsonl`), so a pause is recorded exactly once. CLI commands read the runs table and the event log; they spawn workers but never import LangGraph at module level. `attach` streams events and, when the run pauses, asks the user for a decision and spawns a resume worker; `resume --action` does the same non-interactively.

**Tech Stack:** Python 3.14, LangGraph 1.1.10 + `SqliteSaver` (verified: `delete_thread`, `.conn`, invoking a finished thread returns its final state), `subprocess` (detached sessions), `signal`, `threading` (heartbeat), typer/click prompts, pytest (+ `pytest-xdist` at the end).

**Spec:** `docs/superpowers/specs/2026-09-23-phil-v1-design.md` (§3 UX, §4 worker, §10 failures)
**Inputs:** `docs/superpowers/plans/2026-09-24-phil-03a-followups.md` ("Plan 3b" section)
**User decisions (2026-09-24):** start runs with `phil run plan.json`; one worker per stretch; answer pauses both interactively in `attach` and with `resume --action`; `phil clean` removes everything except `summary.md` (`--purge` removes that too).

## Global Constraints

- Python `>=3.14`; `uv`; source in `src/phil/`, tests in `tests/`; run `uv run pytest`.
- `phil.cli.main` must not import `langgraph`, `langchain*`, or `deepagents` at module level (the existing lazy-import test enforces this). Import `phil.run.worker`, `phil.run.checkpoint`, and anything else that pulls LangGraph inside command functions.
- The engine never sets `state="escalated"`; `phil.run.runner` does, once per pause, and appends one `escalation` event.
- Run state changes go through `update_run`, which enforces the transition table in Task 1.
- Tests never touch the real `~/.phil` (autouse `phil_home`), never read the machine's global git config (autouse `isolated_git_config`), never call a real model. Subprocess tests select scripted agents with `PHIL_AGENT_FACTORY` / `PHIL_TEST_SCENARIO`.
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` after a blank line.

## File Structure

```
src/phil/__main__.py                 create: `python -m phil`
src/phil/store/runs.py               modify: RunState values, TRANSITIONS, InvalidTransition
src/phil/store/events.py             create: EventLog, run_events()
src/phil/run/engine.py               modify: RunDeps.events, node/state events, escalate no longer sets "escalated",
                                             pin red snapshot ref, tester refused notes, escalation log path
src/phil/run/runner.py               modify: record escalation (row + event) when a run pauses
src/phil/workspace/worktree.py       modify: pin_ref(), delete_refs()
src/phil/workspace/shell.py          modify: active process-group registry, kill_active_groups()
src/phil/run/launch.py               create: prepare_run(), worker_command(), spawn_worker(), is_worker_alive()
src/phil/run/worker.py               create: run_worker(), Heartbeat, StopRequested, WorkerError
src/phil/cli/attach.py               create: AttachIO, attach(), render_event()
src/phil/cli/main.py                 modify: _worker (hidden), run, resume, attach, stop, diff, clean
tests/run/worker_scenarios.py        create: scripted factories for subprocess tests
tests/...                            create/modify per task
pyproject.toml                       modify (Task 11): pytest-xdist
```

---

### Task 1: Run-state transitions, the event log, and recording pauses

**Files:**
- Modify: `src/phil/store/runs.py`, `src/phil/run/engine.py`, `src/phil/run/runner.py`, `tests/run/conftest.py`, `tests/run/test_engine_escalation.py`
- Create: `src/phil/store/events.py`, `tests/store/test_events.py`
- Test: `tests/store/test_runs.py` (append), `tests/run/test_runner.py` (append), `tests/run/test_engine_happy.py` (append)

**Interfaces:**
- Produces:
  - `RunState = Literal["pending", "running", "escalated", "completed", "failed", "aborted", "stopped", "cleaned"]`; `TRANSITIONS: dict[str, set[str]]`; `class InvalidTransition(ValueError)`. `update_run(..., state=X)` raises `InvalidTransition` unless `X` equals the current state or is in `TRANSITIONS[current]`.
  - `phil.store.events.EventLog(path)`: `append(kind: str, **data) -> None` (one JSON line with `ts` and `kind`), `read(offset: int = 0) -> tuple[list[dict], int]` (complete lines only; returns the new byte offset), `latest(kind: str) -> dict | None`. `run_events(paths: ProjectPaths, run_id: str) -> EventLog` (file `runs/<id>/events.jsonl`).
  - `RunDeps.events: EventLog | None = None`; `RunEngine._update_run` appends `{"kind": "node", "node": ...}` when `current_node` changes and `{"kind": "state", "state": ..., "needs_attention": ...}` when `state` changes.
  - `escalate` sets only `current_node="escalate"` before `interrupt()`; after a valid answer it still sets `state="running", needs_attention=None`.
  - `runner._drive`, when the graph pauses, sets `state="escalated"` and `needs_attention` (the payload's `error` if present, else its `summary`) on the run row and appends `{"kind": "escalation", "escalation": <payload>}`.

Transition table:

| from | allowed to |
|---|---|
| pending | running, failed, aborted, stopped |
| running | escalated, completed, failed, aborted, stopped |
| escalated | running, failed, aborted, stopped |
| stopped | running, aborted, cleaned |
| failed | running, aborted, cleaned |
| completed | cleaned |
| aborted | cleaned |
| cleaned | — |

(A transition to the same state is always allowed, so a re-run node or a repeated `finish` is harmless.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_runs.py`:

```python
from phil.store.runs import InvalidTransition


def test_valid_transitions(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    for state in ["running", "escalated", "running", "stopped", "running", "completed", "cleaned"]:
        assert update_run(conn, "r-0001", state=state).state == state


def test_same_state_is_allowed(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    update_run(conn, "r-0001", state="running")
    update_run(conn, "r-0001", state="completed")
    assert update_run(conn, "r-0001", state="completed").state == "completed"


def test_invalid_transitions_are_rejected(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    with pytest.raises(InvalidTransition, match="pending to completed"):
        update_run(conn, "r-0001", state="completed")
    update_run(conn, "r-0001", state="running")
    update_run(conn, "r-0001", state="completed")
    with pytest.raises(InvalidTransition):
        update_run(conn, "r-0001", state="running")
```

`tests/store/test_events.py`:

```python
from phil.store.events import EventLog, run_events
from phil.store.paths import ProjectPaths


def test_append_and_read_with_offsets(tmp_path):
    log = EventLog(tmp_path / "run" / "events.jsonl")
    assert log.read() == ([], 0)
    log.append("node", node="setup")
    events, offset = log.read()
    assert [(e["kind"], e["node"]) for e in events] == [("node", "setup")]
    assert "ts" in events[0]
    log.append("state", state="running")
    more, offset2 = log.read(offset)
    assert [e["kind"] for e in more] == ["state"]
    assert log.read(offset2) == ([], offset2)


def test_partial_lines_are_not_returned(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"ts": "t", "kind": "node", "node": "a"}\n{"ts": "t", "kind": "no')
    events, offset = EventLog(path).read()
    assert [e["node"] for e in events] == ["a"]
    assert offset == len('{"ts": "t", "kind": "node", "node": "a"}\n')


def test_latest_by_kind(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append("escalation", escalation={"summary": "first"})
    log.append("node", node="x")
    log.append("escalation", escalation={"summary": "second"})
    assert log.latest("escalation")["escalation"]["summary"] == "second"
    assert log.latest("outcome") is None


def test_run_events_path(phil_home):
    paths = ProjectPaths("demo-12345678")
    assert run_events(paths, "r-0001").path == paths.run_dir("r-0001") / "events.jsonl"
```

In `tests/run/conftest.py`, pass `events=EventLog(paths.run_dir(RUN_ID) / "events.jsonl")` to `RunDeps` in `make_harness` (import `EventLog` from `phil.store.events`).

Append to `tests/run/test_engine_happy.py`:

```python
def test_nodes_and_states_are_logged(make_harness):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]})
    harness.start()
    events, _ = harness.deps.events.read()
    nodes = [e["node"] for e in events if e["kind"] == "node"]
    assert nodes[:4] == ["setup", "pick_task", "implement", "verify"]
    assert nodes[-1] == "finish"
    assert [e["state"] for e in events if e["kind"] == "state"] == ["running", "completed"]
```

(import `review`, `tester_report` from `tests.run.conftest` if not already imported.)

Append to `tests/run/test_runner.py`:

```python
def test_pause_is_recorded_once_by_the_runner(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    paused = outcome_start(harness)
    record = harness.run_record()
    assert (record.state, record.needs_attention) == ("escalated", paused.escalation["summary"])
    escalations = [e for e in harness.deps.events.read()[0] if e["kind"] == "escalation"]
    assert [e["escalation"]["reason"] for e in escalations] == ["attempts"]
```

In `tests/run/test_engine_escalation.py`: the engine no longer marks the row `escalated` when driven directly with `graph.invoke`. In `test_three_failed_greens_escalate`, delete the two lines that read `record = harness.run_record()` and assert `(record.state, record.needs_attention)`; in `test_invalid_action_asks_again_and_a_valid_one_still_works`, delete `assert harness.run_record().state == "escalated"`. (The runner test above now covers the row.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/store tests/run/test_runner.py tests/run/test_engine_happy.py -q`
Expected: FAIL (`ImportError` for `InvalidTransition` / `phil.store.events`; `RunDeps` rejects `events`).

- [ ] **Step 3: Implement**

`src/phil/store/runs.py`: replace `RunState` and add, above `update_run`:

```python
RunState = Literal["pending", "running", "escalated", "completed", "failed", "aborted", "stopped", "cleaned"]

TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "failed", "aborted", "stopped"},
    "running": {"escalated", "completed", "failed", "aborted", "stopped"},
    "escalated": {"running", "failed", "aborted", "stopped"},
    "stopped": {"running", "aborted", "cleaned"},
    "failed": {"running", "aborted", "cleaned"},
    "completed": {"cleaned"},
    "aborted": {"cleaned"},
    "cleaned": set(),
}


class InvalidTransition(ValueError):
    pass
```

and in `update_run`, after the unknown-field check:

```python
    if "state" in fields:
        current = get_run(conn, run_id)
        if current is None:
            raise KeyError(run_id)
        new_state = fields["state"]
        if new_state != current.state and new_state not in TRANSITIONS.get(current.state, set()):
            raise InvalidTransition(f"run {run_id} cannot go from {current.state} to {new_state}")
```

`src/phil/store/events.py`:

```python
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
```

`src/phil/run/engine.py`: import `EventLog` from `phil.store.events`; add `events: EventLog | None = None` as the last `RunDeps` field; replace `_update_run` with:

```python
    def _update_run(self, **fields: object) -> None:
        update_run(self.deps.conn, self.deps.run_id, **fields)
        events = self.deps.events
        if events is None:
            return
        if "current_node" in fields:
            events.append("node", node=fields["current_node"])
        if "state" in fields:
            events.append("state", state=fields["state"], needs_attention=fields.get("needs_attention"))
```

In `escalate`, replace the first `_update_run(state="escalated", current_node="escalate", needs_attention=...)` call with `self._update_run(current_node="escalate")`.

`src/phil/run/runner.py`: import `update_run` from `phil.store.runs` and change `_drive`:

```python
def _drive(engine: RunEngine, graph: Any, payload: Any) -> RunOutcome:
    config = thread_config(engine.deps.run_id)
    graph.invoke(payload, config)
    snapshot = graph.get_state(config)
    if snapshot.interrupts:
        escalation = snapshot.interrupts[0].value
        update_run(
            engine.deps.conn,
            engine.deps.run_id,
            state="escalated",
            needs_attention=escalation.get("error") or escalation["summary"],
        )
        if engine.deps.events is not None:
            engine.deps.events.append("escalation", escalation=escalation)
        return RunOutcome(status="escalated", escalation=escalation)
    return RunOutcome(status=snapshot.values.get("status", "completed"))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all PASS. If another existing test asserted the row was `escalated` after a direct `graph.invoke`, move that assertion to a runner-driven test the same way.

- [ ] **Step 5: Commit**

```bash
git add src/phil/store src/phil/run/engine.py src/phil/run/runner.py tests/store tests/run
git commit -m "$(printf 'Validate run-state transitions, log run events, and record pauses in the runner\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 2: Engine follow-ups — pinned red snapshot, tester refusals, escalation log path

**Files:**
- Modify: `src/phil/workspace/worktree.py`, `src/phil/run/engine.py`
- Test: `tests/workspace/test_worktree.py` (append), `tests/run/test_engine_happy.py` (append), `tests/run/test_engine_tester.py` (append), `tests/run/test_engine_escalation.py` (append)

**Interfaces:**
- Produces: `WorktreeManager.pin_ref(ref: str, sha: str) -> None` (`git update-ref` in the main repo); `WorktreeManager.delete_refs(prefix: str) -> list[str]` (deletes every ref under `prefix`, returns them). When the red gate passes, the engine pins `red_tree` at `refs/phil/<run-id>/red`. `_run_tester` adds a minor open issue `tester command refused: <cmd>` for each `log.refused` entry. The `attempts` escalation payload gains `"log": <path of the last test log or None>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workspace/test_worktree.py`:

```python
def test_pin_and_delete_refs(setup, git_repo):
    manager, worktree, base = setup
    tree = manager.snapshot(worktree.path)
    manager.pin_ref("refs/phil/r-0001/red", tree)
    manager.pin_ref("refs/phil/r-0001/other", base)
    assert run_git(git_repo, "cat-file", "-t", "refs/phil/r-0001/red").strip() == "tree"
    removed = manager.delete_refs("refs/phil/r-0001/")
    assert sorted(removed) == ["refs/phil/r-0001/other", "refs/phil/r-0001/red"]
    assert run_git(git_repo, "for-each-ref", "refs/phil/").strip() == ""
```

Append to `tests/run/test_engine_happy.py`:

```python
def test_red_snapshot_is_pinned(make_harness, calc_repo):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]})
    harness.start()
    assert run_git(calc_repo, "cat-file", "-t", "refs/phil/r-0001/red").strip() == "tree"
```

(import `run_git` from `tests.helpers`.)

Append to `tests/run/test_engine_tester.py`:

```python
def test_refused_tester_commands_are_reported(make_harness):
    def forbidden(turn):
        turn.tools["run_shell"]("python -c 'print(1)'")
        return tester_report()

    harness = make_harness({"implementer": [write_red, write_green], "tester": [forbidden], "reviewer": [review()]})
    final = harness.start()
    assert "tester command refused: python -c 'print(1)'" in [issue["note"] for issue in final["open_issues"]]
```

(import `review` from `tests.run.conftest` if needed.)

Append to `tests/run/test_engine_escalation.py`:

```python
def test_attempts_escalation_points_at_the_last_test_log(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["log"].endswith(".log")
    assert Path(escalation["log"]).exists()
```

(import `Path` from `pathlib`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/workspace/test_worktree.py tests/run -q -k "pin or refused or last_test_log"`
Expected: FAIL (`AttributeError: 'WorktreeManager' object has no attribute 'pin_ref'`; missing note; missing `log` key).

- [ ] **Step 3: Implement**

Append to `WorktreeManager`:

```python
    def pin_ref(self, ref: str, sha: str) -> None:
        git(self.repo_root, "update-ref", ref, sha)

    def delete_refs(self, prefix: str) -> list[str]:
        refs = git(self.repo_root, "for-each-ref", "--format=%(refname)", prefix).split()
        for ref in refs:
            git(self.repo_root, "update-ref", "-d", ref)
        return refs
```

In `RunEngine.verify`, in the red-passed branch (`if not problems:` under `state["phase"] == "red"`), add before the `return`:

```python
                tree = self.worktrees.snapshot(worktree)
                # An unreferenced tree can be garbage-collected while the run sits paused.
                self.worktrees.pin_ref(f"refs/phil/{self.deps.run_id}/red", tree)
```

and change `"red_tree": self.worktrees.snapshot(worktree),` to `"red_tree": tree,`.

In `_run_tester`, after the line that adds `tester command not approved` notes, add:

```python
        notes += [Issue(severity="minor", note=f"tester command refused: {cmd}") for cmd in log.refused]
```

In `_failed_attempt`, add to the `attempts` escalation dict: `"log": (report or {}).get("log_path") or None,`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/workspace/worktree.py src/phil/run/engine.py tests
git commit -m "$(printf 'Pin the red snapshot, report refused tester commands, and link escalations to test logs\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 3: Track and kill child process groups

**Files:**
- Modify: `src/phil/workspace/shell.py`
- Test: `tests/workspace/test_shell.py` (append)

**Interfaces:**
- Produces: `phil.workspace.shell.kill_active_groups(sig: int = signal.SIGKILL) -> list[int]` — sends `sig` to every process group started by `run_command` that has not finished, and forgets them. `run_command` registers each child's group while it runs.

- [ ] **Step 1: Write the failing test** — append to `tests/workspace/test_shell.py`:

```python
import threading
import time

from phil.workspace import shell as shell_module


def test_kill_active_groups_stops_running_commands(tmp_path):
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            run_command(f'{PY} -c "import time; time.sleep(30)"', cwd=tmp_path, timeout_s=60)
        )
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not shell_module._ACTIVE_GROUPS and time.monotonic() < deadline:
        time.sleep(0.05)
    assert shell_module._ACTIVE_GROUPS
    killed = shell_module.kill_active_groups()
    thread.join(timeout=10)
    assert killed
    assert results and not results[0].ok
    assert not shell_module._ACTIVE_GROUPS
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/workspace/test_shell.py -q -k kill_active`
Expected: FAIL (`AttributeError: module 'phil.workspace.shell' has no attribute '_ACTIVE_GROUPS'`).

- [ ] **Step 3: Implement** in `src/phil/workspace/shell.py`: add below the imports

```python
_ACTIVE_GROUPS: set[int] = set()


def kill_active_groups(sig: int = signal.SIGKILL) -> list[int]:
    killed: list[int] = []
    for group in list(_ACTIVE_GROUPS):
        try:
            os.killpg(group, sig)
            killed.append(group)
        except ProcessLookupError:
            pass
        _ACTIVE_GROUPS.discard(group)
    return killed
```

In `run_command`, right after the `Popen` succeeds add `_ACTIVE_GROUPS.add(proc.pid)`, and wrap the existing `try: ... communicate ... except subprocess.TimeoutExpired: ...` block in `try: ... finally: _ACTIVE_GROUPS.discard(proc.pid)`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/workspace -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/workspace/shell.py tests/workspace/test_shell.py
git commit -m "$(printf 'Track child process groups so a stopped worker can kill them\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 4: The worker core (in-process)

**Files:**
- Create: `src/phil/run/launch.py` (only `prepare_run` in this task), `src/phil/run/worker.py`, `tests/run/test_worker.py`

**Interfaces:**
- Consumes: Tasks 1–3; `runner`; `open_checkpointer`; `load_config`; `resolve_repo`; `ScriptedAgentFactory`.
- Produces:
  - `phil.run.launch.prepare_run(info: RepoInfo, plan: Plan, base_sha: str) -> RunRecord` — allocates a run id, writes `plan.json` into the run dir, creates the run row (`pending`).
  - `phil.run.worker`: `HEARTBEAT_S = 5.0`; `class StopRequested(BaseException)`; `class WorkerError(Exception)`; `class Heartbeat(db_path, run_id, interval_s)` with `start()`/`stop()` (its own connection; updates `heartbeat_at`); `run_worker(repo_root: Path, run_id: str, mode: str, decision: dict | None = None, *, factory=None, heartbeat_s: float = HEARTBEAT_S, sleep=time.sleep) -> RunOutcome`.
  - `run_worker` behaviour: rejects unknown modes (`ValueError`); raises `WorkerError` without touching the row for an unknown run, a finished run (`completed`/`aborted`/`cleaned`), `resume` when no decision is pending, and `continue` when one is pending. Otherwise sets `state="running"`, `pid`, `heartbeat_at`, clears `needs_attention`, logs a `worker` event, starts the heartbeat, and drives: `start` on a fresh thread → `runner.start` (plan from the run dir; `test_cmd` from the plan or `[project] test_cmd`); `start` on an existing thread → `runner.continue_run`; `resume` → `runner.resume`; `continue` → `runner.continue_run`. Logs an `outcome` event. On `StopRequested` (SIGTERM while running): kills child groups, sets `stopped` with `needs_attention="stopped by user"`, returns `RunOutcome("stopped")`. On any other exception: kills child groups, sets `failed` with `needs_attention="worker failed: <Type>: <message>"` (≤ 500 chars), logs a `state` event, re-raises. Always: stops the heartbeat, clears `pid`, closes both connections, restores the previous SIGTERM handler.

- [ ] **Step 1: Write the failing tests** — `tests/run/test_worker.py`:

```python
import time

import pytest

from phil.agents.fake import ScriptedAgentFactory
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import Heartbeat, StopRequested, WorkerError, run_worker
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from tests.helpers import run_git
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red


def happy():
    return ScriptedAgentFactory(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )


def new_run(repo):
    info = resolve_repo(repo)
    return info, prepare_run(info, calc_plan(), info.head_sha)


def row(info, run_id):
    return get_run(connect(ProjectPaths(info.slug).db_path), run_id)


def test_prepare_run_writes_the_plan_and_a_pending_row(calc_repo):
    info, record = new_run(calc_repo)
    assert record.state == "pending"
    assert (ProjectPaths(info.slug).run_dir(record.run_id) / "plan.json").exists()


def test_start_runs_to_completion_and_cleans_up(calc_repo):
    info, record = new_run(calc_repo)
    outcome = run_worker(calc_repo, record.run_id, "start", factory=happy(), heartbeat_s=0.05)
    assert outcome.status == "completed"
    final = row(info, record.run_id)
    assert (final.state, final.pid) == ("completed", None)
    assert final.heartbeat_at is not None
    kinds = [e["kind"] for e in run_events(ProjectPaths(info.slug), record.run_id).read()[0]]
    assert kinds[0] == "worker" and kinds[-1] == "outcome"
    assert run_git(calc_repo, "log", "--format=%s", record.branch).splitlines()[0] == "CALC-001: Add subtract"


def test_escalate_then_resume(calc_repo):
    info, record = new_run(calc_repo)
    escalating = ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]})
    assert run_worker(calc_repo, record.run_id, "start", factory=escalating).status == "escalated"
    assert row(info, record.run_id).state == "escalated"
    finishing = ScriptedAgentFactory(
        {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    assert run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=finishing).status == "completed"


def test_mode_guards_do_not_touch_the_row(calc_repo):
    info, record = new_run(calc_repo)
    with pytest.raises(WorkerError, match="not waiting for a decision"):
        run_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, factory=happy())
    assert row(info, record.run_id).state == "pending"
    with pytest.raises(WorkerError, match="unknown run"):
        run_worker(calc_repo, "r-ffff", "start", factory=happy())
    with pytest.raises(ValueError):
        run_worker(calc_repo, record.run_id, "sideways", factory=happy())


def test_finished_runs_are_rejected(calc_repo):
    info, record = new_run(calc_repo)
    run_worker(calc_repo, record.run_id, "start", factory=happy())
    with pytest.raises(WorkerError, match="finished"):
        run_worker(calc_repo, record.run_id, "continue", factory=happy())


def test_crash_marks_failed_and_continue_recovers(calc_repo):
    info, record = new_run(calc_repo)
    crashing = ScriptedAgentFactory({"implementer": [write_red, RuntimeError("model went away")]})
    with pytest.raises(RuntimeError):
        run_worker(calc_repo, record.run_id, "start", factory=crashing)
    failed = row(info, record.run_id)
    assert failed.state == "failed"
    assert failed.needs_attention == "worker failed: RuntimeError: model went away"
    recovering = ScriptedAgentFactory(
        {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    assert run_worker(calc_repo, record.run_id, "continue", factory=recovering).status == "completed"


def test_stop_marks_stopped_and_continue_recovers(calc_repo):
    info, record = new_run(calc_repo)

    def interrupted(turn):
        raise StopRequested()

    stopping = ScriptedAgentFactory({"implementer": [write_red, interrupted]})
    assert run_worker(calc_repo, record.run_id, "start", factory=stopping).status == "stopped"
    stopped = row(info, record.run_id)
    assert (stopped.state, stopped.needs_attention, stopped.pid) == ("stopped", "stopped by user", None)
    recovering = ScriptedAgentFactory(
        {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    assert run_worker(calc_repo, record.run_id, "continue", factory=recovering).status == "completed"


def test_heartbeat_updates_the_row(calc_repo):
    info, record = new_run(calc_repo)
    db_path = ProjectPaths(info.slug).db_path
    beat = Heartbeat(db_path, record.run_id, 0.02)
    beat.start()
    time.sleep(0.2)
    beat.stop()
    assert row(info, record.run_id).heartbeat_at is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_worker.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'phil.run.launch'`).

- [ ] **Step 3: Implement**

`src/phil/run/launch.py`:

```python
from phil.contracts import Plan
from phil.repo import RepoInfo
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import RunRecord, create_run, new_run_id


def prepare_run(info: RepoInfo, plan: Plan, base_sha: str) -> RunRecord:
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    try:
        run_id = new_run_id(conn)
        ArtifactStore(paths.run_dir(run_id)).write_plan(plan)
        return create_run(
            conn,
            run_id=run_id,
            keyword=plan.keyword,
            base_sha=base_sha,
            worktree=paths.worktree_dir(run_id),
            tasks_total=len(plan.tasks),
            story_ref=plan.story_ref,
        )
    finally:
        conn.close()
```

`src/phil/run/worker.py`:

```python
import os
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

from phil.agents.invoke import AgentFactory
from phil.config import load_config
from phil.repo import resolve_repo
from phil.run import runner
from phil.run.checkpoint import open_checkpointer
from phil.run.engine import RunDeps, RunEngine
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect, utcnow
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run, update_run
from phil.workspace.shell import kill_active_groups

HEARTBEAT_S = 5.0
MODES = ("start", "resume", "continue")
FINISHED = ("completed", "aborted", "cleaned")


class StopRequested(BaseException):
    """Raised in the worker's main thread when it receives SIGTERM."""


class WorkerError(Exception):
    pass


def _raise_stop(signum: int, frame: object) -> None:
    raise StopRequested()


class Heartbeat:
    def __init__(self, db_path: Path, run_id: str, interval_s: float) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, args=(db_path, run_id, interval_s), daemon=True)
        self._started = False

    def _beat(self, db_path: Path, run_id: str, interval_s: float) -> None:
        conn = connect(db_path)
        try:
            while not self._stop.wait(interval_s):
                update_run(conn, run_id, heartbeat_at=utcnow())
        finally:
            conn.close()

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._started:
            self._thread.join(timeout=5)


def run_worker(
    repo_root: Path,
    run_id: str,
    mode: str,
    decision: dict | None = None,
    *,
    factory: AgentFactory | None = None,
    heartbeat_s: float = HEARTBEAT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> runner.RunOutcome:
    if mode not in MODES:
        raise ValueError(f"unknown worker mode {mode!r}; expected one of {MODES}")
    info = resolve_repo(repo_root)
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    record = get_run(conn, run_id)
    if record is None:
        conn.close()
        raise WorkerError(f"unknown run {run_id}")
    if record.state in FINISHED:
        conn.close()
        raise WorkerError(f"{run_id} is {record.state}; the run is finished")
    config = load_config(info.root)
    events = run_events(paths, run_id)
    deps = RunDeps(
        config=config,
        conn=conn,
        repo_root=info.root,
        run_id=run_id,
        worktree=Path(record.worktree),
        artifacts=ArtifactStore(paths.run_dir(run_id)),
        factory=factory,
        sleep=sleep,
        events=events,
    )
    engine = RunEngine(deps)
    saver = open_checkpointer(paths.db_path)
    graph = engine.build(saver)
    heartbeat = Heartbeat(paths.db_path, run_id, heartbeat_s)
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGTERM, _raise_stop)
    try:
        snapshot = graph.get_state(runner.thread_config(run_id))
        if mode == "resume" and not snapshot.interrupts:
            raise WorkerError(f"{run_id} is not waiting for a decision")
        if mode == "continue" and snapshot.interrupts:
            raise WorkerError(f"{run_id} is waiting for a decision; resume it with an action")
        update_run(conn, run_id, state="running", pid=os.getpid(), heartbeat_at=utcnow(), needs_attention=None)
        events.append("worker", mode=mode, pid=os.getpid())
        heartbeat.start()
        if mode == "start" and not snapshot.values:
            plan = deps.artifacts.read_plan()
            test_cmd = plan.test_cmd or config.project.test_cmd or ""
            outcome = runner.start(engine, graph, plan=plan, base_sha=record.base_sha, test_cmd=test_cmd)
        elif mode == "resume":
            outcome = runner.resume(engine, graph, decision or {})
        else:
            outcome = runner.continue_run(engine, graph)
        events.append("outcome", status=outcome.status)
        return outcome
    except WorkerError:
        raise
    except StopRequested:
        kill_active_groups()
        update_run(conn, run_id, state="stopped", needs_attention="stopped by user")
        events.append("state", state="stopped", needs_attention="stopped by user")
        return runner.RunOutcome(status="stopped")
    except Exception as exc:
        kill_active_groups()
        message = f"worker failed: {type(exc).__name__}: {exc}"[:500]
        update_run(conn, run_id, state="failed", needs_attention=message)
        events.append("state", state="failed", needs_attention=message)
        raise
    finally:
        heartbeat.stop()
        update_run(conn, run_id, pid=None)
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
        saver.conn.close()
        conn.close()
```

(`update_run(conn, run_id, pid=None)` in `finally` runs even after a `WorkerError`; it only clears `pid` and does not change `state`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/run/test_worker.py -q`, then `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/launch.py src/phil/run/worker.py tests/run/test_worker.py
git commit -m "$(printf 'Add the run worker with heartbeat, stop, and failure handling\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 5: Detached workers — spawning, liveness, and `python -m phil _worker`

**Files:**
- Create: `src/phil/__main__.py`, `tests/run/worker_scenarios.py`, `tests/run/test_launch.py`
- Modify: `src/phil/run/launch.py`, `src/phil/cli/main.py`

**Interfaces:**
- Produces:
  - `phil.run.launch.worker_command(repo_root: Path, run_id: str, mode: str, decision: dict | None = None) -> list[str]` → `[sys.executable, "-m", "phil", "--repo", str(repo_root), "_worker", run_id, "--mode", mode]` plus `["--decision", json.dumps(decision)]` when given.
  - `spawn_worker(repo_root: Path, run_id: str, mode: str, decision: dict | None = None, *, env: dict | None = None) -> subprocess.Popen` — detached (`start_new_session=True`), `stdin` from `/dev/null`, stdout+stderr appended to `runs/<id>/logs/worker.log`, `cwd` the repo root.
  - `is_worker_alive(record: RunRecord, *, stale_after_s: float = 30.0) -> bool` — false without a pid or when the process is gone; false when `heartbeat_at` is older than `stale_after_s`.
  - `phil _worker RUN_ID --mode MODE [--decision JSON]` (hidden command). If `PHIL_AGENT_FACTORY=module:attr` is set, the worker calls that zero-argument factory function to get its agent factory (test hook); otherwise real agents are used. Exit code 2 on `WorkerError` (message printed), non-zero with a traceback on other errors.

- [ ] **Step 1: Write the scenarios module** — `tests/run/worker_scenarios.py`:

```python
import os
import time

from phil.agents.fake import ScriptedAgentFactory
from tests.run.conftest import bad_green, review, tester_report, write_green, write_red


def _slow(turn):
    time.sleep(60)
    return write_red(turn)


SCENARIOS = {
    "happy": lambda: {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
    "escalate": lambda: {"implementer": [write_red, bad_green, bad_green, bad_green]},
    "finish_after_retry": lambda: {"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]},
    "slow": lambda: {"implementer": [_slow]},
}


def factory() -> ScriptedAgentFactory:
    return ScriptedAgentFactory(SCENARIOS[os.environ["PHIL_TEST_SCENARIO"]]())
```

- [ ] **Step 2: Write the failing tests** — `tests/run/test_launch.py`:

```python
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from phil.repo import resolve_repo
from phil.run.launch import is_worker_alive, prepare_run, spawn_worker, worker_command
from phil.store.db import connect, utcnow
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from tests.run.conftest import calc_plan

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def worker_env(scenario: str) -> dict:
    return os.environ | {
        "PHIL_AGENT_FACTORY": "tests.run.worker_scenarios:factory",
        "PHIL_TEST_SCENARIO": scenario,
        "PYTHONPATH": str(PROJECT_ROOT),
    }


def new_run(repo):
    info = resolve_repo(repo)
    return info, prepare_run(info, calc_plan(), info.head_sha)


def test_worker_command_shape(tmp_path):
    command = worker_command(tmp_path, "r-0001", "resume", {"action": "retry"})
    assert command[:3] == [sys.executable, "-m", "phil"]
    assert command[3:] == ["--repo", str(tmp_path), "_worker", "r-0001", "--mode", "resume", "--decision", '{"action": "retry"}']


def test_detached_worker_completes_a_run(calc_repo):
    info, record = new_run(calc_repo)
    proc = spawn_worker(calc_repo, record.run_id, "start", env=worker_env("happy"))
    assert proc.wait(timeout=180) == 0
    paths = ProjectPaths(info.slug)
    assert get_run(connect(paths.db_path), record.run_id).state == "completed"
    assert (paths.run_dir(record.run_id) / "logs" / "worker.log").exists()


def test_detached_escalation_then_resume(calc_repo):
    info, record = new_run(calc_repo)
    paths = ProjectPaths(info.slug)
    assert spawn_worker(calc_repo, record.run_id, "start", env=worker_env("escalate")).wait(timeout=180) == 0
    assert get_run(connect(paths.db_path), record.run_id).state == "escalated"
    assert run_events(paths, record.run_id).latest("escalation")["escalation"]["reason"] == "attempts"
    proc = spawn_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, env=worker_env("finish_after_retry"))
    assert proc.wait(timeout=180) == 0
    assert get_run(connect(paths.db_path), record.run_id).state == "completed"


def test_worker_error_exits_2(calc_repo):
    info, record = new_run(calc_repo)
    proc = spawn_worker(calc_repo, record.run_id, "resume", {"action": "retry"}, env=worker_env("happy"))
    assert proc.wait(timeout=60) == 2


def test_is_worker_alive(calc_repo):
    info, record = new_run(calc_repo)
    assert not is_worker_alive(record)
    me = replace(record, pid=os.getpid(), heartbeat_at=utcnow())
    assert is_worker_alive(me)
    assert not is_worker_alive(replace(me, heartbeat_at="2000-01-01T00:00:00+00:00"))
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    assert not is_worker_alive(replace(me, pid=done.pid))
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/run/test_launch.py -q`
Expected: FAIL (`ImportError: cannot import name 'spawn_worker'`).

- [ ] **Step 4: Implement**

Append to `src/phil/run/launch.py` (add imports `json`, `os`, `subprocess`, `sys`, `from datetime import UTC, datetime`, `from pathlib import Path`, `from phil.repo import resolve_repo`):

```python
def worker_command(repo_root: Path, run_id: str, mode: str, decision: dict | None = None) -> list[str]:
    command = [sys.executable, "-m", "phil", "--repo", str(repo_root), "_worker", run_id, "--mode", mode]
    if decision is not None:
        command += ["--decision", json.dumps(decision)]
    return command


def spawn_worker(
    repo_root: Path, run_id: str, mode: str, decision: dict | None = None, *, env: dict | None = None
) -> subprocess.Popen:
    info = resolve_repo(repo_root)
    log_path = ProjectPaths(info.slug).run_dir(run_id) / "logs" / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        return subprocess.Popen(
            worker_command(info.root, run_id, mode, decision),
            cwd=info.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )


def is_worker_alive(record: RunRecord, *, stale_after_s: float = 30.0) -> bool:
    if record.pid is None:
        return False
    try:
        os.kill(record.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    if record.heartbeat_at is None:
        return True
    age = (datetime.now(UTC) - datetime.fromisoformat(record.heartbeat_at)).total_seconds()
    return age <= stale_after_s
```

`src/phil/__main__.py`:

```python
from phil.cli.main import app

if __name__ == "__main__":
    app()
```

In `src/phil/cli/main.py` add (imports `importlib`, `json`, `os` at the top; keep LangGraph out of module level):

```python
def _factory_from_env():
    target = os.environ.get("PHIL_AGENT_FACTORY")
    if not target:
        return None
    module_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_name), attr)()


@app.command("_worker", hidden=True)
def worker(
    ctx: typer.Context,
    run_id: str,
    mode: str = typer.Option(..., "--mode"),
    decision: str | None = typer.Option(None, "--decision"),
) -> None:
    """Drive a run until it pauses, finishes, stops, or fails (internal)."""
    from phil.run.worker import WorkerError, run_worker

    start = ctx.obj.get("repo") or Path.cwd()
    try:
        outcome = run_worker(start, run_id, mode, json.loads(decision) if decision else None, factory=_factory_from_env())
    except WorkerError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    console.print(f"{escape(run_id)}: {escape(outcome.status)}")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/run/test_launch.py tests/test_cli.py -q`, then `uv run pytest -q`
Expected: all PASS (the lazy-import test still passes).

- [ ] **Step 6: Commit**

```bash
git add src/phil/__main__.py src/phil/run/launch.py src/phil/cli/main.py tests/run/worker_scenarios.py tests/run/test_launch.py
git commit -m "$(printf 'Spawn detached workers and detect whether they are alive\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 6: `phil run plan.json`

**Files:**
- Modify: `src/phil/cli/main.py`
- Test: `tests/cli/__init__.py` (empty), `tests/cli/test_run_command.py`

**Interfaces:**
- Produces: `phil run PLAN_FILE [--base REF] [--foreground]`. Validates the plan (`Plan.model_validate_json`), loads config (`ConfigError` → exit 1), requires a test command (plan's `test_cmd` or `[project] test_cmd`, else exit 1 with `plan has no test_cmd and phil.toml sets no [project] test_cmd`), resolves the base (`--base` via `git rev-parse <ref>^{commit}`, else `HEAD`), warns about uncommitted files when starting from `HEAD`, prints a note when `[git] sign_commits` is not `false` or `run_hooks` is true (`Commit signing or hooks are on; a failing signature or hook will pause the run.`), calls `prepare_run`, then either `spawn_worker(..., "start")` and prints `Run <id> started. Follow it with \`phil attach <id>\`.`, or with `--foreground` runs `run_worker` in this process and prints the outcome.

- [ ] **Step 1: Write the failing tests** — `tests/cli/test_run_command.py`:

```python
import json

from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from tests.helpers import run_git
from tests.run.conftest import calc_plan

runner = CliRunner()


def plan_file(tmp_path, **overrides):
    path = tmp_path / "plan.json"
    path.write_text(calc_plan().model_copy(update=overrides).model_dump_json())
    return path


def runs_for(repo):
    return list_runs(connect(ProjectPaths(resolve_repo(repo).slug).db_path))


def test_run_spawns_a_worker(calc_repo, tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(cli, "spawn_worker", lambda repo, run_id, mode, *a, **k: spawned.append((run_id, mode)))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(plan_file(tmp_path))])
    assert result.exit_code == 0, result.output
    [record] = runs_for(calc_repo)
    assert spawned == [(record.run_id, "start")]
    assert f"phil attach {record.run_id}" in result.output
    assert record.base_sha == run_git(calc_repo, "rev-parse", "HEAD").strip()


def test_run_requires_a_test_command(calc_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(plan_file(tmp_path, test_cmd=None))])
    assert result.exit_code == 1
    assert "no test_cmd" in result.output
    assert runs_for(calc_repo) == []


def test_run_rejects_an_invalid_plan(calc_repo, tmp_path):
    bad = tmp_path / "plan.json"
    bad.write_text(json.dumps({"keyword": "calc"}))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(bad)])
    assert result.exit_code == 1
    assert "invalid plan" in result.output


def test_run_warns_about_uncommitted_files(calc_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: None)
    (calc_repo / "scratch.txt").write_text("wip")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(plan_file(tmp_path))])
    assert "1 uncommitted file" in result.output


def test_run_in_the_foreground(calc_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_AGENT_FACTORY", "tests.run.worker_scenarios:factory")
    monkeypatch.setenv("PHIL_TEST_SCENARIO", "happy")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", "--foreground", str(plan_file(tmp_path))])
    assert result.exit_code == 0, result.output
    [record] = runs_for(calc_repo)
    assert record.state == "completed"
    assert "completed" in result.output
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli -q`
Expected: FAIL (`No such command 'run'`).

- [ ] **Step 3: Implement** in `src/phil/cli/main.py` (imports at module level are allowed for `phil.config`, `phil.contracts`, `phil.git`, `phil.run.launch`, `pydantic.ValidationError` — none load LangGraph):

```python
from pydantic import ValidationError

from phil.config import ConfigError, load_config
from phil.contracts import Plan
from phil.git import GitError, git
from phil.run.launch import is_worker_alive, prepare_run, spawn_worker


@app.command("run")
def run_plan(
    ctx: typer.Context,
    plan_file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Plan JSON file."),
    base: str | None = typer.Option(None, "--base", help="Start from this ref instead of HEAD."),
    foreground: bool = typer.Option(False, "--foreground", help="Run in this process instead of a background worker."),
) -> None:
    """Start a run from a plan file."""
    info, _ = _open_project(ctx)
    try:
        plan = Plan.model_validate_json(plan_file.read_text())
    except ValidationError as exc:
        console.print(f"[phil.error]invalid plan: {escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    try:
        config = load_config(info.root)
    except ConfigError as exc:
        console.print(f"[phil.error]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    if not (plan.test_cmd or config.project.test_cmd):
        console.print("[phil.error]plan has no test_cmd and phil.toml sets no [project] test_cmd[/]")
        raise typer.Exit(1)
    if base is not None:
        try:
            base_sha = git(info.root, "rev-parse", f"{base}^{{commit}}").strip()
        except GitError as exc:
            console.print(f"[phil.error]{escape(str(exc))}[/]")
            raise typer.Exit(1) from exc
    else:
        base_sha = info.head_sha
        if info.dirty_files:
            count = len(info.dirty_files)
            console.print(
                f"[phil.warn]⚠ {count} uncommitted file{'s' if count != 1 else ''} not included "
                f"(the run starts from {base_sha[:8]})[/]"
            )
    if config.git.sign_commits is not False or config.git.run_hooks:
        console.print("[phil.muted]Commit signing or hooks are on; a failing signature or hook will pause the run.[/]")
    record = prepare_run(info, plan, base_sha)
    if foreground:
        from phil.run.worker import run_worker

        outcome = run_worker(info.root, record.run_id, "start", factory=_factory_from_env())
        console.print(f"Run [phil.id]{escape(record.run_id)}[/]: {escape(outcome.status)}")
        return
    spawn_worker(info.root, record.run_id, "start")
    console.print(
        f"Run [phil.id]{escape(record.run_id)}[/] started. Follow it with `phil attach {escape(record.run_id)}`."
    )
```

(Note: `config.git.sign_commits` defaults to `"auto"`, which is not `False`, so the note shows by default; that is intended — "auto" signs when the repo signs.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cli tests/test_cli.py -q`, then `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/cli/main.py tests/cli
git commit -m "$(printf 'Add phil run to start a run from a plan file\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 7: `phil resume`

**Files:**
- Modify: `src/phil/cli/main.py`
- Test: `tests/cli/test_resume_command.py`

**Interfaces:**
- Produces: `phil resume RUN [--action A] [--hint H]`.
  - Unknown run → exit 1. Worker alive → exit 1 (`<id> already has a running worker`).
  - `escalated`: reads the latest `escalation` event; without `--action` prints the summary and `choose --action: <options>` and exits 2; an action not in the options exits 2; otherwise spawns `resume` with `{"action": A}` plus `"hint": H` when given.
  - `failed`, `stopped`, or `running` with a dead worker: `--action` is an error (exit 2, `nothing to answer; the run continues from its last checkpoint`); otherwise spawns `continue`.
  - Any other state → exit 1 (`<id> is <state>; nothing to resume`).

- [ ] **Step 1: Write the failing tests** — `tests/cli/test_resume_command.py`:

```python
import pytest
from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import update_run
from tests.run.conftest import calc_plan

runner = CliRunner()


@pytest.fixture
def run(calc_repo, monkeypatch):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    paths = ProjectPaths(info.slug)
    spawned = []
    monkeypatch.setattr(cli, "spawn_worker", lambda repo, run_id, mode, decision=None, **k: spawned.append((mode, decision)))
    return calc_repo, record.run_id, connect(paths.db_path), run_events(paths, record.run_id), spawned


def invoke(repo, *args):
    return runner.invoke(cli.app, ["--repo", str(repo), "resume", *args])


def escalate(conn, events, run_id):
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="escalated", needs_attention="CALC-001 failed 3 attempts in the green phase")
    events.append("escalation", escalation={"summary": "CALC-001 failed 3 attempts", "options": ["retry", "skip", "abort"]})


def test_escalated_run_needs_an_action(run):
    repo, run_id, conn, events, spawned = run
    escalate(conn, events, run_id)
    result = invoke(repo, run_id)
    assert result.exit_code == 2
    assert "retry, skip, abort" in result.output
    assert spawned == []


def test_escalated_run_resumes_with_action_and_hint(run):
    repo, run_id, conn, events, spawned = run
    escalate(conn, events, run_id)
    result = invoke(repo, run_id, "--action", "retry", "--hint", "use a minus sign")
    assert result.exit_code == 0, result.output
    assert spawned == [("resume", {"action": "retry", "hint": "use a minus sign"})]


def test_unknown_action_is_rejected(run):
    repo, run_id, conn, events, spawned = run
    escalate(conn, events, run_id)
    assert invoke(repo, run_id, "--action", "approve").exit_code == 2
    assert spawned == []


def test_failed_run_continues(run):
    repo, run_id, conn, events, spawned = run
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="failed", needs_attention="worker failed: RuntimeError: x")
    assert invoke(repo, run_id).exit_code == 0
    assert spawned == [("continue", None)]
    assert invoke(repo, run_id, "--action", "retry").exit_code == 2


def test_finished_or_pending_runs_are_not_resumed(run):
    repo, run_id, conn, events, spawned = run
    result = invoke(repo, run_id)
    assert result.exit_code == 1
    assert "nothing to resume" in result.output
    assert invoke(repo, "r-ffff").exit_code == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_resume_command.py -q`
Expected: FAIL (`No such command 'resume'`).

- [ ] **Step 3: Implement** in `src/phil/cli/main.py` (import `get_run` from `phil.store.runs` and `run_events` from `phil.store.events`):

```python
def _require_run(conn: sqlite3.Connection, run_id: str):
    record = get_run(conn, run_id)
    if record is None:
        console.print(f"[phil.error]unknown run {escape(run_id)}[/]")
        raise typer.Exit(1)
    return record


@app.command()
def resume(
    ctx: typer.Context,
    run_id: str,
    action: str | None = typer.Option(None, "--action", help="Answer a paused run (e.g. retry, skip, approve)."),
    hint: str | None = typer.Option(None, "--hint", help="Hint for the next attempt (with --action retry)."),
) -> None:
    """Answer a paused run, or continue a failed or stopped one."""
    info, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    if is_worker_alive(record):
        console.print(f"[phil.error]{escape(run_id)} already has a running worker[/]")
        raise typer.Exit(1)
    if record.state == "escalated":
        latest = run_events(ProjectPaths(info.slug), run_id).latest("escalation")
        escalation = latest["escalation"] if latest else {"summary": record.needs_attention or "", "options": []}
        options = escalation["options"]
        if action is None:
            console.print(escape(escalation["summary"]))
            console.print(f"[phil.error]choose --action: {escape(', '.join(options))}[/]")
            raise typer.Exit(2)
        if action not in options:
            console.print(f"[phil.error]unknown action {escape(action)!r}; choose one of: {escape(', '.join(options))}[/]")
            raise typer.Exit(2)
        decision = {"action": action} | ({"hint": hint} if hint else {})
        spawn_worker(info.root, run_id, "resume", decision)
        console.print(f"Resuming [phil.id]{escape(run_id)}[/] with {escape(action)}.")
        return
    if record.state in ("failed", "stopped", "running"):
        if action is not None:
            console.print("[phil.error]nothing to answer; the run continues from its last checkpoint[/]")
            raise typer.Exit(2)
        spawn_worker(info.root, run_id, "continue")
        console.print(f"Continuing [phil.id]{escape(run_id)}[/] from its last checkpoint.")
        return
    console.print(f"[phil.error]{escape(run_id)} is {escape(record.state)}; nothing to resume[/]")
    raise typer.Exit(1)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cli -q`, then `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/cli/main.py tests/cli/test_resume_command.py
git commit -m "$(printf 'Add phil resume to answer or continue a run\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 8: `phil attach`

**Files:**
- Create: `src/phil/cli/attach.py`, `tests/cli/test_attach.py`
- Modify: `src/phil/cli/main.py`

**Interfaces:**
- Produces:
  - `phil.cli.attach.AttachIO(choose: Callable[[str, list[str]], str], ask_hint: Callable[[], str | None], spawn: Callable[[str, dict | None], object], sleep: Callable[[float], None] = time.sleep, alive: Callable[[RunRecord], bool] = is_worker_alive)`.
  - `render_event(console, event) -> None` — `node` → `· <node>` (muted), `state` → `state: <state>` plus `needs_attention`, `escalation` → the summary and, when present, `error` (warn style), `worker`/`outcome` → muted one-liners.
  - `attach(conn, run_id, events: EventLog, console, io: AttachIO, *, poll_s: float = 1.0, start_timeout_s: float = 30.0) -> str` — loops: prints new events; returns the state when the run is `completed`/`aborted`/`cleaned` (after printing the summary path); when `escalated` and no worker is alive, asks for a decision from the latest escalation (`io.choose(prompt, options)`; for `retry` also `io.ask_hint()`), calls `io.spawn("resume", decision)`, then waits until the row leaves `escalated` (returns `"escalated"` with a warning pointing at `logs/worker.log` if it does not within `start_timeout_s`); when `failed`/`stopped`, or `running`/`pending` with no live worker for longer than `start_timeout_s`, prints `Continue with \`phil resume <id>\`` and returns the state; otherwise sleeps `poll_s`.
  - `phil attach RUN`: wires `AttachIO` to `click.Choice` prompts and `spawn_worker`.

- [ ] **Step 1: Write the failing tests** — `tests/cli/test_attach.py`:

```python
from typer.testing import CliRunner

from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.cli.attach import AttachIO, attach
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.ui.theme import make_console
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red


def escalated_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    run_worker(calc_repo, record.run_id, "start", factory=ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]}))
    return info, record


def finishing():
    return ScriptedAgentFactory({"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]})


def test_attach_answers_an_escalation_and_follows_to_the_end(calc_repo):
    info, record = escalated_run(calc_repo)
    paths = ProjectPaths(info.slug)
    asked = []
    io = AttachIO(
        choose=lambda prompt, options: asked.append((prompt, options)) or "retry",
        ask_hint=lambda: "use a minus sign",
        spawn=lambda mode, decision: run_worker(calc_repo, record.run_id, mode, decision, factory=finishing()),
        sleep=lambda _: None,
    )
    console = make_console(record=True, width=120)
    state = attach(connect(paths.db_path), record.run_id, run_events(paths, record.run_id), console, io, poll_s=0)
    assert state == "completed"
    assert asked[0][1] == ["retry", "skip", "abort"]
    text = console.export_text()
    assert "CALC-001 failed 3 attempts in the green phase" in text
    assert "summary.md" in text


def test_attach_stops_at_a_failed_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    try:
        run_worker(calc_repo, record.run_id, "start", factory=ScriptedAgentFactory({"implementer": [RuntimeError("boom")]}))
    except RuntimeError:
        pass
    paths = ProjectPaths(info.slug)
    console = make_console(record=True, width=120)
    io = AttachIO(choose=lambda p, o: "abort", ask_hint=lambda: None, spawn=lambda m, d: None, sleep=lambda _: None)
    assert attach(connect(paths.db_path), record.run_id, run_events(paths, record.run_id), console, io, poll_s=0) == "failed"
    assert f"phil resume {record.run_id}" in console.export_text()


def test_attach_command_prompts_for_a_decision(calc_repo, monkeypatch):
    info, record = escalated_run(calc_repo)
    monkeypatch.setattr(
        cli, "spawn_worker",
        lambda repo, run_id, mode, decision=None, **k: run_worker(repo, run_id, mode, decision, factory=finishing()),
    )
    result = CliRunner().invoke(cli.app, ["--repo", str(calc_repo), "attach", record.run_id], input="retry\nuse a minus sign\n")
    assert result.exit_code == 0, result.output
    assert get_run(connect(ProjectPaths(info.slug).db_path), record.run_id).state == "completed"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_attach.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'phil.cli.attach'`).

- [ ] **Step 3: Implement** — `src/phil/cli/attach.py`:

```python
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Console
from rich.markup import escape

from phil.run.launch import is_worker_alive
from phil.store.events import EventLog
from phil.store.runs import RunRecord, get_run

TERMINAL = ("completed", "aborted", "cleaned")


@dataclass
class AttachIO:
    choose: Callable[[str, list[str]], str]
    ask_hint: Callable[[], str | None]
    spawn: Callable[[str, dict | None], object]
    sleep: Callable[[float], None] = time.sleep
    alive: Callable[[RunRecord], bool] = field(default=is_worker_alive)


def render_event(console: Console, event: dict) -> None:
    kind = event["kind"]
    if kind == "node":
        console.print(f"[phil.muted]· {escape(str(event['node']))}[/]")
    elif kind == "state":
        note = f" — {escape(event['needs_attention'])}" if event.get("needs_attention") else ""
        console.print(f"state: [phil.id]{escape(event['state'])}[/]{note}")
    elif kind == "escalation":
        escalation = event["escalation"]
        console.print(f"[phil.warn]⏸ {escape(escalation['summary'])}[/]")
        if escalation.get("error"):
            console.print(f"[phil.error]{escape(escalation['error'])}[/]")
    elif kind == "worker":
        console.print(f"[phil.muted]worker {event.get('pid')} ({escape(str(event.get('mode')))})[/]")
    elif kind == "outcome":
        console.print(f"[phil.muted]worker finished: {escape(str(event.get('status')))}[/]")


def _prompt(escalation: dict) -> str:
    return f"{escalation['summary']} — what next"


def attach(
    conn: sqlite3.Connection,
    run_id: str,
    events: EventLog,
    console: Console,
    io: AttachIO,
    *,
    poll_s: float = 1.0,
    start_timeout_s: float = 30.0,
) -> str:
    offset = 0
    idle_since = time.monotonic()
    while True:
        new, offset = events.read(offset)
        for event in new:
            render_event(console, event)
        record = get_run(conn, run_id)
        assert record is not None
        if record.state in TERMINAL:
            console.print(f"[phil.muted]Summary: {escape(str(events.path.parent / 'summary.md'))}[/]")
            return record.state
        alive = io.alive(record)
        if record.state == "escalated" and not alive:
            latest = events.latest("escalation")
            escalation = latest["escalation"] if latest else {"summary": record.needs_attention or "", "options": ["abort"]}
            action = io.choose(_prompt(escalation), escalation["options"])
            decision: dict = {"action": action}
            if action == "retry":
                hint = io.ask_hint()
                if hint:
                    decision["hint"] = hint
            io.spawn("resume", decision)
            deadline = time.monotonic() + start_timeout_s
            while get_run(conn, run_id).state == "escalated":
                if time.monotonic() > deadline:
                    console.print(f"[phil.warn]The worker did not start; see {escape(str(events.path.parent / 'logs' / 'worker.log'))}[/]")
                    return "escalated"
                io.sleep(poll_s)
            idle_since = time.monotonic()
            continue
        if record.state in ("failed", "stopped"):
            console.print(f"Continue with `phil resume {escape(run_id)}`.")
            return record.state
        if record.state in ("running", "pending") and not alive:
            if time.monotonic() - idle_since > start_timeout_s:
                console.print(f"[phil.warn]No worker is running.[/] Continue with `phil resume {escape(run_id)}`.")
                return record.state
        else:
            idle_since = time.monotonic()
        io.sleep(poll_s)
```

In `src/phil/cli/main.py` add (import `click` at the top; `phil.cli.attach` has no LangGraph imports):

```python
@app.command("attach")
def attach_command(ctx: typer.Context, run_id: str) -> None:
    """Follow a run and answer it when it pauses."""
    from phil.cli.attach import AttachIO, attach

    info, conn = _open_project(ctx)
    _require_run(conn, run_id)

    def choose(prompt: str, options: list[str]) -> str:
        return typer.prompt(prompt, type=click.Choice(options))

    def ask_hint() -> str | None:
        return typer.prompt("Hint for the next attempt (optional)", default="", show_default=False) or None

    io = AttachIO(choose=choose, ask_hint=ask_hint, spawn=lambda mode, decision: spawn_worker(info.root, run_id, mode, decision))
    attach(conn, run_id, run_events(ProjectPaths(info.slug), run_id), console, io)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cli -q`, then `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/cli/attach.py src/phil/cli/main.py tests/cli/test_attach.py
git commit -m "$(printf 'Add phil attach to follow a run and answer its pauses\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 9: `phil stop`

**Files:**
- Modify: `src/phil/cli/main.py`
- Test: `tests/cli/test_stop_command.py`

**Interfaces:**
- Produces: `phil stop RUN [--timeout SECONDS]`. `escalated` → exit 1 with `waiting for your decision; stop it with \`phil resume <id> --action abort\``. Not `running`/`pending` → exit 1 (`<id> is <state>; nothing to stop`). Live worker → SIGTERM, then poll until the row is `stopped` (exit 0) or the timeout passes (exit 1, `the worker did not stop in time`). No live worker → set `stopped` with `needs_attention="stopped by user (worker was not running)"`. On success prints `Stopped <id>. Continue with \`phil resume <id>\`.`

- [ ] **Step 1: Write the failing tests** — `tests/cli/test_stop_command.py`:

```python
import time

from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.launch import is_worker_alive, prepare_run, spawn_worker
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run, update_run
from tests.run.conftest import calc_plan
from tests.run.test_launch import worker_env

runner = CliRunner()


def new_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    return info, record, connect(ProjectPaths(info.slug).db_path)


def test_stop_a_live_worker(calc_repo):
    info, record, conn = new_run(calc_repo)
    proc = spawn_worker(calc_repo, record.run_id, "start", env=worker_env("slow"))
    deadline = time.monotonic() + 60
    while not is_worker_alive(get_run(conn, record.run_id)) and time.monotonic() < deadline:
        time.sleep(0.2)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert result.exit_code == 0, result.output
    assert proc.wait(timeout=30) == 0
    stopped = get_run(conn, record.run_id)
    assert (stopped.state, stopped.pid) == ("stopped", None)


def test_stop_without_a_worker_marks_stopped(calc_repo):
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert result.exit_code == 0
    assert get_run(conn, record.run_id).needs_attention == "stopped by user (worker was not running)"


def test_stop_refuses_paused_and_finished_runs(calc_repo):
    info, record, conn = new_run(calc_repo)
    update_run(conn, record.run_id, state="running")
    update_run(conn, record.run_id, state="escalated")
    paused = runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id])
    assert paused.exit_code == 1
    assert "--action abort" in paused.output
    update_run(conn, record.run_id, state="aborted")
    assert runner.invoke(cli.app, ["--repo", str(calc_repo), "stop", record.run_id]).exit_code == 1
```

Risk to check while implementing: SIGTERM raises `StopRequested` in the worker's main thread. LangGraph's sync `invoke` runs a step with a single task inline, so the scripted agent's `time.sleep` is interrupted directly. If the test shows the node running in a pool thread instead, the main thread still receives `StopRequested` while waiting on it; report what you observe rather than changing the design.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_stop_command.py -q`
Expected: FAIL (`No such command 'stop'`).

- [ ] **Step 3: Implement** in `src/phil/cli/main.py` (imports `signal`, `time`; `update_run` from `phil.store.runs`):

```python
@app.command()
def stop(
    ctx: typer.Context,
    run_id: str,
    timeout: float = typer.Option(15.0, "--timeout", help="Seconds to wait for the worker to stop."),
) -> None:
    """Stop a running run; continue it later with `phil resume`."""
    _, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    if record.state == "escalated":
        console.print(
            f"[phil.error]{escape(run_id)} is waiting for your decision; "
            f"stop it with `phil resume {escape(run_id)} --action abort`[/]"
        )
        raise typer.Exit(1)
    if record.state not in ("running", "pending"):
        console.print(f"[phil.error]{escape(run_id)} is {escape(record.state)}; nothing to stop[/]")
        raise typer.Exit(1)
    if is_worker_alive(record):
        os.kill(record.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while get_run(conn, run_id).state != "stopped":
            if time.monotonic() > deadline:
                console.print("[phil.error]the worker did not stop in time[/]")
                raise typer.Exit(1)
            time.sleep(0.2)
    else:
        update_run(conn, run_id, state="stopped", needs_attention="stopped by user (worker was not running)")
    console.print(f"Stopped [phil.id]{escape(run_id)}[/]. Continue with `phil resume {escape(run_id)}`.")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cli -q`, then `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/cli/main.py tests/cli/test_stop_command.py
git commit -m "$(printf 'Add phil stop to stop a running worker\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 10: `phil diff` and `phil clean`

**Files:**
- Modify: `src/phil/cli/main.py`
- Test: `tests/cli/test_diff_clean.py`

**Interfaces:**
- Produces:
  - `phil diff RUN` — prints `git diff <base_sha> <branch>` from the repo root as plain text; exit 1 with `the run's branch no longer exists` if git fails.
  - `phil clean RUN [--purge]` — refuses (exit 1) while the run is `pending`, `running`, or `escalated`; otherwise removes the worktree (if present), deletes the branch (ignoring a missing branch), deletes `refs/phil/<id>/*`, deletes the run's checkpoints (`open_checkpointer(...).delete_thread(run_id)`, imported inside the function), removes everything in the run dir except `summary.md` (with `--purge`, removes the run dir entirely), and sets the row to `cleaned`. Prints what it removed.

- [ ] **Step 1: Write the failing tests** — `tests/cli/test_diff_clean.py`:

```python
from typer.testing import CliRunner

from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.checkpoint import open_checkpointer
from phil.run.launch import prepare_run
from phil.run.runner import thread_config
from phil.run.worker import run_worker
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from tests.helpers import run_git
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red

runner = CliRunner()


def finished_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    factory = ScriptedAgentFactory({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]})
    run_worker(calc_repo, record.run_id, "start", factory=factory)
    return info, record, ProjectPaths(info.slug)


def test_diff_shows_the_run_changes(calc_repo):
    info, record, paths = finished_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "diff", record.run_id])
    assert result.exit_code == 0
    assert "def subtract" in result.output


def test_clean_keeps_only_the_summary(calc_repo):
    info, record, paths = finished_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", record.run_id])
    assert result.exit_code == 0, result.output
    run_dir = paths.run_dir(record.run_id)
    assert [p.name for p in run_dir.iterdir()] == ["summary.md"]
    assert not paths.worktree_dir(record.run_id).exists()
    assert run_git(calc_repo, "branch", "--list", record.branch).strip() == ""
    assert run_git(calc_repo, "for-each-ref", f"refs/phil/{record.run_id}/").strip() == ""
    saver = open_checkpointer(paths.db_path)
    assert saver.get_tuple(thread_config(record.run_id)) is None
    assert get_run(connect(paths.db_path), record.run_id).state == "cleaned"
    assert runner.invoke(cli.app, ["--repo", str(calc_repo), "diff", record.run_id]).exit_code == 1


def test_clean_purge_removes_the_run_dir(calc_repo):
    info, record, paths = finished_run(calc_repo)
    assert runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", "--purge", record.run_id]).exit_code == 0
    assert not paths.run_dir(record.run_id).exists()


def test_clean_refuses_a_paused_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    run_worker(calc_repo, record.run_id, "start", factory=ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]}))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", record.run_id])
    assert result.exit_code == 1
    assert "escalated" in result.output
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_diff_clean.py -q`
Expected: FAIL (`No such command 'diff'`).

- [ ] **Step 3: Implement** in `src/phil/cli/main.py` (imports `shutil`; `Worktree`, `WorktreeManager` from `phil.workspace.worktree`):

```python
@app.command()
def diff(ctx: typer.Context, run_id: str) -> None:
    """Show the changes a run made, compared with its base."""
    info, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    try:
        typer.echo(git(info.root, "diff", record.base_sha, record.branch), nl=False)
    except GitError as exc:
        console.print("[phil.error]the run's branch no longer exists[/]")
        raise typer.Exit(1) from exc


@app.command()
def clean(
    ctx: typer.Context,
    run_id: str,
    purge: bool = typer.Option(False, "--purge", help="Also delete the run summary."),
) -> None:
    """Remove a finished run's worktree, branch, checkpoints, and scratch files."""
    from phil.run.checkpoint import open_checkpointer

    info, conn = _open_project(ctx)
    record = _require_run(conn, run_id)
    if record.state in ("pending", "running", "escalated"):
        console.print(
            f"[phil.error]{escape(run_id)} is {escape(record.state)}; finish or stop it first "
            f"(`phil stop {escape(run_id)}` or `phil resume {escape(run_id)} --action abort`)[/]"
        )
        raise typer.Exit(1)
    paths = ProjectPaths(info.slug)
    manager = WorktreeManager(info.root)
    worktree = Path(record.worktree)
    if worktree.exists():
        manager.remove(Worktree(worktree, record.branch, record.base_sha), delete_branch=False)
    try:
        git(info.root, "branch", "-D", record.branch)
    except GitError:
        pass
    manager.delete_refs(f"refs/phil/{run_id}/")
    saver = open_checkpointer(paths.db_path)
    try:
        saver.delete_thread(run_id)
    finally:
        saver.conn.close()
    run_dir = paths.run_dir(run_id)
    if run_dir.exists():
        if purge:
            shutil.rmtree(run_dir)
        else:
            for child in run_dir.iterdir():
                if child.name == "summary.md":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    update_run(conn, run_id, state="cleaned", needs_attention=None)
    kept = "" if purge else " (kept summary.md)"
    console.print(f"Cleaned [phil.id]{escape(run_id)}[/]{kept}.")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cli -q`, then `uv run pytest -q`
Expected: all PASS (the lazy-import test still passes because `open_checkpointer` is imported inside `clean`).

- [ ] **Step 5: Commit**

```bash
git add src/phil/cli/main.py tests/cli/test_diff_clean.py
git commit -m "$(printf 'Add phil diff and phil clean\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 11: Run the suite in parallel

**Files:**
- Modify: `pyproject.toml`, `README.md`

**Interfaces:** none.

- [ ] **Step 1: Add pytest-xdist**

```bash
uv add --dev pytest-xdist
```

In `pyproject.toml` under `[tool.pytest.ini_options]`, change `addopts` to `"-m 'not live' -n auto"`.

- [ ] **Step 2: Verify**

Run: `uv run pytest -q` and note the wall time; run it twice to catch order-dependent failures.
Expected: all PASS both times, faster than the serial run (~2.5 min before this plan). If a test fails only in parallel, report it (do not add `-p no:xdist` or skip it) — it indicates shared state.

- [ ] **Step 3: Document** — in `README.md` under `## Development`, add:

```markdown
Tests run in parallel with pytest-xdist; use `uv run pytest -p no:xdist` to run serially when debugging.

Start a run from a plan file and follow it:

    uv run phil run plan.json
    uv run phil attach <run-id>
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock README.md
git commit -m "$(printf 'Run the test suite in parallel\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

## Spec coverage for this plan

| Spec / follow-up | Covered here | Deferred to |
|---|---|---|
| §3 UX: `run`, `attach`, `resume`, `stop`, `diff`, `clean` | Tasks 6–10 | Chat start (`start_run`): plan 4 |
| §4 detached worker, crash → resume from checkpoint | Tasks 4, 5, 7 | — |
| §10 worker crash detection via heartbeat | Tasks 4, 5, 8 | — |
| §10 abort keeps the worktree; `clean` removes it | Tasks 8–10 | MVP lifecycle: automatic cleanup after merge |
| Follow-up: exceptions escape the runner → worker records `failed` | Task 4 | — |
| Follow-up: valid run-state transitions | Task 1 | — |
| Follow-up: start once per thread; check pending interrupt before resume | Task 4 | — |
| Follow-up: stop kills child process groups | Tasks 3, 4, 9 | — |
| Follow-up: connection lifetimes; heartbeat connection; delete checkpoints on clean | Tasks 4, 10 | — |
| Follow-up: pin the red snapshot | Task 2 | — |
| Follow-up: resume inputs from the run row; detect a removed worktree before resuming | Task 4; final review fix A3 | — |
| Follow-up: escalation payload links to logs | Task 2 | Multiple reports per escalation: later |
| Follow-up: tester refused commands reported | Task 2 | — |
| Follow-up: suite speed | Task 11 | — |
| Follow-up: usage callback, SDK timeout, 200-with-error, lean architect | — | Plan 4 |
