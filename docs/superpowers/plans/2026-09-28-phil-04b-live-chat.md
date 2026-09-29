# Phil Plan 4b: Live Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the chat live: a bottom toolbar with animated status, planning that runs in the background while the prompt stays usable, the chat's own run followed in place (progress, pauses answered in the chat, completion), `/btw` side questions, and reopening a chat later — one goal per chat.

**Architecture:** `prompt_toolkit` prompt on the main thread; model calls on a small thread pool; one `RunWatcher` thread tailing the chat's run. Everything reports through one queue of `ChatEvent`s that the `ChatController` drains between inputs; a background event wakes the prompt (keeping typed text) so notices print immediately and the prompt can change (e.g. to `Approve?` or a pause question). A lock-protected `ChatState` feeds the toolbar. The chat ↔ run link is a `runs.chat_id` column plus `chats/<id>/state.json`.

**Tech Stack:** Python 3.14, `prompt_toolkit` (new dependency), rich (still formats output; printed above the prompt via `patch_stdout(raw=True)`), `concurrent.futures.ThreadPoolExecutor`, `threading`, existing `invoke_agent`, `EventLog`, launch/attach helpers.

**Spec:** `docs/superpowers/specs/2026-09-28-phil-04b-live-chat-design.md` (binding), with `docs/superpowers/specs/2026-09-23-phil-v1-design.md` §3/§6.

**One deviation from the spec, decided here:** the spec proposed a `ChatOutput` adapter that captures Rich output as ANSI and prints it with `print_formatted_text`. Rich resolves `sys.stdout` at print time, so under `patch_stdout(raw=True)` a normal Rich console already prints above the prompt; no adapter is needed. The terminal IO (Task 8) verifies this and falls back to the adapter only if colours or layout break.

## Global Constraints

- Python `>=3.14`; `uv`; `uv run pytest -q` (parallel; `-n 0` for serial debugging). Keep dependencies current (`uv add prompt-toolkit` takes the latest).
- `phil.cli.main`, `phil.agents.invoke`, `phil.packets`, `phil.chat.*`, `phil.ui.*` must not import `langgraph`, `langchain*`, or `deepagents` at module level; `prompt_toolkit` is imported only inside the terminal IO (Task 8) so tests and non-TTY use never need it.
- **One chat = one goal at a time.** A chat follows at most one run. A new goal while a goal is being planned asks to replace it; while the chat's run is working, a new goal is refused with a pointer to another window.
- Worker threads never print. They post `ChatEvent`s; only the main thread prints. Shared state goes through `ChatState` (locked).
- Approval stays code-recorded from the user's literal answer (`y`/`yes`), and the 4a launch checks (`launch_problems`, API keys) still gate every run start. `/btw` never changes a plan or a run.
- Chat agents never read the working tree: the architect and `/btw` read the base-commit snapshot (`export_tree`).
- Escape all user/agent text printed through rich (`escape(repr(x))`, never `escape(x)!r`; TOML section names like `[models]` escaped).
- Tests never touch the real `~/.phil`, never read global git config, never call a real model (ScriptedAgentFactory keyed by spec name). Controller tests drive a scripted IO with inline jobs — no real threads, no sleeps.
- Every commit message ends with a blank line then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

```
pyproject.toml / uv.lock                modify (Task 8): prompt-toolkit
src/phil/store/db.py                    modify: migration — runs.chat_id
src/phil/store/runs.py                  modify: RunRecord.chat_id; create_run(chat_id=)
src/phil/run/launch.py                  modify: prepare_run(chat_id=)
src/phil/chat/events.py                 create: ChatEvent
src/phil/chat/state.py                  create: RunView, ToolbarView, ChatState
src/phil/chat/session.py                modify: state.json save/load, ChatSession.open, list_open_chats
src/phil/ui/toolbar.py                  create: render_toolbar
src/phil/chat/planning.py               modify: Planner on_step callback
src/phil/chat/watcher.py                create: RunWatcher
src/phil/contracts/inputs.py            modify: BtwInput
src/phil/agents/registry.py             modify: "btw" spec
src/phil/prompts/btw.md                 create
src/phil/chat/btw.py                    create: ask_btw
src/phil/ui/brief_view.py               create: render_brief
src/phil/chat/controller.py             rewrite (Tasks 6–7): staged, event-driven controller
src/phil/chat/terminal.py               create (Task 8): TerminalIO (prompt_toolkit), LineIO
src/phil/cli/main.py                    modify: open-chat list, --resume, --new, terminal wiring
tests/chat/*, tests/store/*, tests/ui/*  per task
docs/…                                  Task 9
```

---

### Task 1: Link runs to the chat that started them

**Files:** Modify `src/phil/store/db.py`, `src/phil/store/runs.py`, `src/phil/run/launch.py`; Test `tests/store/test_runs.py`, `tests/run/test_launch.py` (append).

**Interfaces — Produces:** migration adding `runs.chat_id TEXT` (nullable); `RunRecord.chat_id: str | None` (last field, default `None`); `create_run(..., chat_id: str | None = None)`; `prepare_run(info, plan, base_sha, *, chat_id: str | None = None)`.

- [ ] **Step 1: Failing tests**

`tests/store/test_runs.py` (append):

```python
def test_runs_record_the_chat_that_started_them(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1, chat_id="c-1")
    create_run(conn, run_id="r-0002", keyword="MAPS", base_sha="abc", worktree=Path("/wt2"), tasks_total=1)
    assert get_run(conn, "r-0001").chat_id == "c-1"
    assert get_run(conn, "r-0002").chat_id is None
```

`tests/run/test_launch.py` (append):

```python
def test_prepare_run_records_the_chat(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha, chat_id="c-20260928-120000")
    assert record.chat_id == "c-20260928-120000"
```

Also add a migration test: a database created before this migration (run the old migrations only — mirror how existing tests check the `telemetry.call` migration, or open a fresh db and assert `PRAGMA table_info(runs)` contains `chat_id`).

- [ ] **Step 2: Run to verify failure** — `uv run pytest -n 0 -q tests/store/test_runs.py tests/run/test_launch.py -k chat` → FAIL (`unexpected keyword argument 'chat_id'`).

- [ ] **Step 3: Implement**

`src/phil/store/db.py` — append to `MIGRATIONS`: `"ALTER TABLE runs ADD COLUMN chat_id TEXT"`.

`src/phil/store/runs.py` — add `chat_id: str | None = None` as the last `RunRecord` field; `create_run` gains `chat_id: str | None = None` and inserts it (`INSERT INTO runs (..., chat_id) VALUES (..., ?)`).

`src/phil/run/launch.py` — `prepare_run(info, plan, base_sha, *, chat_id=None)` passes `chat_id` to `create_run`.

- [ ] **Step 4: Run tests** — focused tests, then `uv run pytest -q` → PASS.

- [ ] **Step 5: Commit** — `Link runs to the chat that started them`.

---

### Task 2: Chat events, chat state, and persistent chat sessions

**Files:** Create `src/phil/chat/events.py`, `src/phil/chat/state.py`, `tests/chat/test_state.py`; Modify `src/phil/chat/session.py`, `tests/chat/test_overview_session.py` (append).

**Interfaces — Produces:**
- `ChatEvent(kind: str, data: dict = {}, generation: int = -1)` (frozen dataclass; `generation=-1` means "never stale").
- `RunView(run_id, keyword, node: str | None, tasks_done, tasks_total, started: float)` (frozen).
- `ToolbarView(stage: str, step: str | None, step_started: float, run: RunView | None, paused: bool, btw_pending: int, cancelling: bool)` (frozen).
- `ChatState()` with thread-safe `set_stage(stage)`, `set_step(step: str | None, now: float)`, `set_run(run: RunView | None)`, `set_paused(bool)`, `add_btw(delta: int)`, `set_cancelling(bool)`, `view() -> ToolbarView`. Initial stage `"idle"`.
- `ChatSession.save_state(data: dict)` → atomic write of `dir/state.json` (write temp then `os.replace`); `ChatSession.load_state() -> dict` (`{}` if missing/corrupt); `ChatSession.open(paths, chat_id) -> ChatSession` (existing dir; `FileNotFoundError` if absent).
- `ChatSummary(id, objective: str | None, stage: str, run_id: str | None, run_state: str | None)` and `list_open_chats(paths, conn) -> list[ChatSummary]`, newest first: a chat is open when its state has a `run_id` whose completion was not yet shown (`done_seen` false), or its stage is `"approval"` with a stored plan.

- [ ] **Step 1: Failing tests** — `tests/chat/test_state.py`:

```python
import threading

from phil.chat.events import ChatEvent
from phil.chat.state import ChatState, RunView


def test_state_updates_and_view():
    state = ChatState()
    assert state.view().stage == "idle"
    state.set_stage("planning")
    state.set_step("architect", now=10.0)
    state.set_run(RunView("r-1", "CALC", "implement", 1, 2, started=5.0))
    state.set_paused(True)
    state.add_btw(1)
    view = state.view()
    assert (view.stage, view.step, view.step_started) == ("planning", "architect", 10.0)
    assert view.run.run_id == "r-1" and view.paused and view.btw_pending == 1
    state.set_step(None, now=11.0)
    state.add_btw(-1)
    assert state.view().step is None and state.view().btw_pending == 0


def test_state_is_thread_safe():
    state = ChatState()
    threads = [threading.Thread(target=lambda: [state.add_btw(1) for _ in range(1000)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state.view().btw_pending == 4000


def test_events_default_to_never_stale():
    assert ChatEvent("btw_answer").generation == -1
```

`tests/chat/test_overview_session.py` (append):

```python
from phil.chat.session import ChatSession, list_open_chats
from phil.store.db import connect
from phil.store.runs import create_run, update_run


def test_state_round_trip_and_open(git_repo):
    paths = ProjectPaths(resolve_repo(git_repo).slug)
    session = ChatSession.create(paths)
    assert session.load_state() == {}
    session.save_state({"stage": "approval", "goal": {"objective": "x"}})
    again = ChatSession.open(paths, session.id)
    assert again.load_state()["stage"] == "approval"


def test_list_open_chats(git_repo):
    paths = ProjectPaths(resolve_repo(git_repo).slug)
    conn = connect(paths.db_path)
    running = ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 10, 0, 0))
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=paths.worktree_dir("r-0001"), tasks_total=1, chat_id=running.id)
    running.save_state({"stage": "running", "goal": {"objective": "Add divide"}, "run_id": "r-0001", "done_seen": False})
    finished = ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 11, 0, 0))
    finished.save_state({"stage": "idle", "goal": {"objective": "Old"}, "run_id": "r-0001", "done_seen": True})
    approving = ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 12, 0, 0))
    approving.save_state({"stage": "approval", "goal": {"objective": "Add pow"}, "plan": {"keyword": "POW"}})
    ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 13, 0, 0))  # empty chat, never had a goal
    chats = list_open_chats(paths, conn)
    assert [c.id for c in chats] == [approving.id, running.id]
    assert chats[1].objective == "Add divide" and chats[1].run_state == "pending"
```

(`update_run` import may be unused — drop it if so. Import `datetime` at the top of the test module if not already there.)

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError: phil.chat.events`).

- [ ] **Step 3: Implement**

`src/phil/chat/events.py`:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatEvent:
    """Something a background job or the run watcher reports to the chat's main thread."""

    kind: str
    data: dict = field(default_factory=dict)
    # Goal jobs carry the goal generation they belong to; results from a replaced goal are dropped.
    # -1 means the event is never stale (run events, /btw answers).
    generation: int = -1
```

`src/phil/chat/state.py`:

```python
import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RunView:
    run_id: str
    keyword: str
    node: str | None
    tasks_done: int
    tasks_total: int
    started: float  # epoch seconds


@dataclass(frozen=True)
class ToolbarView:
    stage: str = "idle"
    step: str | None = None
    step_started: float = 0.0
    run: RunView | None = None
    paused: bool = False
    btw_pending: int = 0
    cancelling: bool = False


class ChatState:
    """What the toolbar shows; written by worker threads and the main loop, read on every redraw."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._view = ToolbarView()

    def _update(self, **fields: object) -> None:
        with self._lock:
            self._view = replace(self._view, **fields)

    def set_stage(self, stage: str) -> None:
        self._update(stage=stage)

    def set_step(self, step: str | None, now: float) -> None:
        self._update(step=step, step_started=now)

    def set_run(self, run: RunView | None) -> None:
        self._update(run=run)

    def set_paused(self, paused: bool) -> None:
        self._update(paused=paused)

    def set_cancelling(self, cancelling: bool) -> None:
        self._update(cancelling=cancelling)

    def add_btw(self, delta: int) -> None:
        with self._lock:
            self._view = replace(self._view, btw_pending=max(0, self._view.btw_pending + delta))

    def view(self) -> ToolbarView:
        with self._lock:
            return self._view
```

`src/phil/chat/session.py` — add (keep existing members):

```python
import json
import os
import sqlite3

from phil.store.runs import get_run

STATE_FILE = "state.json"


@dataclass(frozen=True)
class ChatSummary:
    id: str
    objective: str | None
    stage: str
    run_id: str | None
    run_state: str | None

# inside ChatSession:
    @classmethod
    def open(cls, paths: ProjectPaths, chat_id: str) -> "ChatSession":
        directory = paths.project_dir / "chats" / chat_id
        if not directory.is_dir():
            raise FileNotFoundError(f"no chat {chat_id}")
        return cls(chat_id, directory, ArtifactStore(directory), EventLog(directory / "transcript.jsonl"))

    def save_state(self, data: dict) -> None:
        target = self.dir / STATE_FILE
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, default=str))
        os.replace(tmp, target)

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
        if not directory.is_dir():
            continue
        state = ChatSession.open(paths, directory.name).load_state()
        run_id = state.get("run_id")
        open_run = bool(run_id) and not state.get("done_seen", False)
        open_plan = state.get("stage") == "approval" and bool(state.get("plan"))
        if not (open_run or open_plan):
            continue
        record = get_run(conn, run_id) if run_id else None
        goal = state.get("goal") or {}
        found.append(
            ChatSummary(directory.name, goal.get("objective"), state.get("stage", "idle"), run_id, record.state if record else None)
        )
    return found
```

(Chat ids sort chronologically by name — `c-YYYYMMDD-HHMMSS[-n]` — so newest-first is a reverse name sort. `-10` sorting after `-9` is acceptable at this scale.)

- [ ] **Step 4: Run tests** — `uv run pytest -q tests/chat`, then the full suite → PASS.

- [ ] **Step 5: Commit** — `Add chat events, chat state, and persistent chat sessions`.

---

### Task 3: Toolbar renderer and planning step reports

**Files:** Create `src/phil/ui/toolbar.py`, `tests/ui/test_toolbar.py`; Modify `src/phil/chat/planning.py`, `tests/chat/test_planning.py` (append).

**Interfaces — Produces:**
- `render_toolbar(view: ToolbarView, now: float) -> str` — plain text; segments joined by `"  │  "`:
  - step: `"<frame> <label> · <elapsed>"`, frame = `SPINNER[int((now - step_started) * 8) % len(SPINNER)]`; labels: `intake → "Understanding the goal"`, `architect → "Architect drafting"`, `critic → "Critic reviewing"`, `revise → "Architect revising"`, `snapshot → "Reading the repo"`, `btw → "Answering /btw"`, else the raw step; when `cancelling`, append `" (cancelling…)"`.
  - run: `"<run_id> · <KEYWORD> <done>/<total> · <node or 'starting'> · <elapsed since run.started>"`.
  - paused: `"⏸ <run_id> needs you (/answer)"`.
  - btw: `"/btw ×<n>"` when `btw_pending > 0` and the current step isn't already `btw`.
  - nothing to show: `"Phil · type a goal, or /help"`.
  - elapsed: `"<s>s"` under a minute, `"<m>m <ss>s"` under an hour, else `"<h>h <mm>m"`.
- `Planner.draft(goal, tree, on_step=None)` / `Planner.revise(goal, draft, feedback, tree, on_step=None)`: `on_step(name)` is called with `"architect"` (or `"revise"` for any architect pass after the first in a cycle) before each architect call and `"critic"` before each critic call.

- [ ] **Step 1: Failing tests** — `tests/ui/test_toolbar.py`:

```python
from phil.chat.state import RunView, ToolbarView
from phil.ui.toolbar import SPINNER, render_toolbar


def test_idle():
    assert render_toolbar(ToolbarView(), now=0.0) == "Phil · type a goal, or /help"


def test_step_spinner_and_elapsed():
    view = ToolbarView(stage="planning", step="architect", step_started=100.0)
    assert render_toolbar(view, now=112.0) == f"{SPINNER[int(12 * 8) % len(SPINNER)]} Architect drafting · 12s"
    assert render_toolbar(view, now=100.0).startswith(SPINNER[0])


def test_run_pause_btw_and_cancelling():
    run = RunView("r-7f3a", "CALC", "implement", 1, 2, started=0.0)
    view = ToolbarView(stage="paused", run=run, paused=True, btw_pending=2)
    text = render_toolbar(view, now=185.0)
    assert "r-7f3a · CALC 1/2 · implement · 3m 05s" in text
    assert "⏸ r-7f3a needs you (/answer)" in text
    assert "/btw ×2" in text
    cancelling = ToolbarView(stage="planning", step="critic", step_started=0.0, cancelling=True)
    assert render_toolbar(cancelling, now=1.0).endswith("Critic reviewing · 1s (cancelling…)")


def test_long_elapsed():
    run = RunView("r-1", "X", None, 0, 1, started=0.0)
    assert "starting · 1h 01m" in render_toolbar(ToolbarView(stage="running", run=run), now=3660.0)
```

`tests/chat/test_planning.py` (append):

```python
def test_planner_reports_steps(chat_ctx, tmp_path):
    factory = ScriptedAgentFactory({
        "architect": [plan(n=1), plan(n=2)],
        "critic": [critique("revise", [issue("too big")]), critique()],
    })
    steps = []
    Planner(chat_ctx(factory), "overview").draft(goal(), tmp_path, on_step=steps.append)
    assert steps == ["architect", "critic", "revise", "critic"]
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** — `src/phil/ui/toolbar.py`:

```python
from phil.chat.state import ToolbarView

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SEPARATOR = "  │  "
STEP_LABELS = {
    "intake": "Understanding the goal",
    "snapshot": "Reading the repo",
    "architect": "Architect drafting",
    "revise": "Architect revising",
    "critic": "Critic reviewing",
    "btw": "Answering /btw",
}


def elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def render_toolbar(view: ToolbarView, now: float) -> str:
    parts: list[str] = []
    if view.step:
        frame = SPINNER[int((now - view.step_started) * 8) % len(SPINNER)]
        label = STEP_LABELS.get(view.step, view.step)
        segment = f"{frame} {label} · {elapsed(now - view.step_started)}"
        if view.cancelling:
            segment += " (cancelling…)"
        parts.append(segment)
    if view.run:
        run = view.run
        parts.append(
            f"{run.run_id} · {run.keyword} {run.tasks_done}/{run.tasks_total} · "
            f"{run.node or 'starting'} · {elapsed(now - run.started)}"
        )
    if view.paused and view.run:
        parts.append(f"⏸ {view.run.run_id} needs you (/answer)")
    if view.btw_pending and view.step != "btw":
        parts.append(f"/btw ×{view.btw_pending}")
    return SEPARATOR.join(parts) if parts else "Phil · type a goal, or /help"
```

`src/phil/chat/planning.py` — thread an optional `on_step: Callable[[str], None] | None` through `draft`, `revise` and `_cycle`; in `_cycle`, call `on_step("architect")` before the first architect pass and `on_step("revise")` before later passes, and `on_step("critic")` before each critic call (guard `if on_step:`). No other behaviour changes; existing tests pass unchanged.

- [ ] **Step 4: Run tests** — `uv run pytest -q tests/ui tests/chat`, then the full suite → PASS.

- [ ] **Step 5: Commit** — `Add the chat toolbar renderer and planning step reports`.

---

### Task 4: The run watcher

**Files:** Create `src/phil/chat/watcher.py`, `tests/chat/test_watcher.py`.

**Interfaces — Consumes:** `EventLog` / `run_events`, `get_run`, `is_worker_alive`, `worker_starting`, `run_totals`, `connect`. **Produces:**

```python
class RunWatcher:
    def __init__(self, paths: ProjectPaths, run_id: str, post: Callable[[ChatEvent], None], *,
                 alive=is_worker_alive, starting=worker_starting, clock=time.time,
                 lost_after_s: float = 30.0, interval_s: float = 1.0) -> None
    def poll_once(self) -> None     # read new events + the row, post what changed
    def start(self) -> None         # daemon thread: poll_once every interval_s until stop() or the run ends
    def stop(self) -> None
    done: bool                      # True after run_done was posted
```

Posted events (all `generation=-1`):
- `run_progress` `{"node", "state", "tasks_done", "tasks_total", "keyword", "started"}` whenever any of node/state/tasks changed (`started` = the row's `created_at` as epoch seconds).
- `run_paused` `{"escalation": <latest escalation payload>}` when the row is `escalated` and no worker is alive or starting — once per escalation (track the latest escalation event's `ts`).
- `run_resumed` `{}` when the row leaves `escalated` after a `run_paused` was posted.
- `run_done` `{"state", "tasks_done", "tasks_total", "needs_attention", "tokens", "cost_usd", "summary": <path of runs/<id>/summary.md>}` when the row is `completed`, `aborted`, `cleaned`, `failed`, or `stopped`; then `done = True` and polling stops. (`failed`/`stopped` are "ended, needs attention" — the chat offers `/resume`.)
- `worker_lost` `{}` once when the row is `running`/`pending`, no worker is alive or starting, and that has lasted longer than `lost_after_s` (clock-based; reset when a worker is seen).
- A missing run row or unreadable log never raises from `poll_once`; the thread body wraps `poll_once` in `try/except Exception` and keeps going.
- The watcher opens its own SQLite connection (`connect(paths.db_path)`) per poll or holds one created on its own thread — never shares the main thread's connection.

- [ ] **Step 1: Failing tests** — `tests/chat/test_watcher.py`:

```python
from phil.chat.watcher import RunWatcher
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import update_run
from tests.run.conftest import calc_plan


def setup(calc_repo, **kw):
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    record = prepare_run(info, calc_plan(), info.head_sha)
    posted = []
    now = [1000.0]
    kw.setdefault("alive", lambda record: False)
    kw.setdefault("starting", lambda events: False)
    watcher = RunWatcher(paths, record.run_id, posted.append, clock=lambda: now[0], **kw)
    return paths, record.run_id, connect(paths.db_path), run_events(paths, record.run_id), watcher, posted, now


def kinds(posted):
    return [e.kind for e in posted]


def test_progress_then_done(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, alive=lambda r: True)
    update_run(conn, run_id, state="running", current_node="implement")
    watcher.poll_once()
    assert kinds(posted) == ["run_progress"] and posted[0].data["node"] == "implement"
    watcher.poll_once()
    assert kinds(posted) == ["run_progress"]  # nothing changed
    update_run(conn, run_id, state="completed", tasks_done=1)
    watcher.poll_once()
    assert kinds(posted)[-1] == "run_done" and posted[-1].data["state"] == "completed"
    assert watcher.done


def test_pause_is_posted_once_and_resume_detected(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="escalated", needs_attention="CALC-001 failed 3 attempts")
    events.append("escalation", escalation={"summary": "CALC-001 failed 3 attempts", "options": ["retry", "skip", "abort"]})
    watcher.poll_once()
    watcher.poll_once()
    assert kinds(posted).count("run_paused") == 1
    assert posted[-1].data["escalation"]["options"] == ["retry", "skip", "abort"]
    update_run(conn, run_id, state="running")
    watcher.poll_once()
    assert "run_resumed" in kinds(posted)


def test_no_pause_while_a_worker_is_alive(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, alive=lambda r: True)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="escalated")
    events.append("escalation", escalation={"summary": "x", "options": ["abort"]})
    watcher.poll_once()
    assert "run_paused" not in kinds(posted)


def test_worker_lost_after_a_grace_period(calc_repo):
    paths, run_id, conn, events, watcher, posted, now = setup(calc_repo, lost_after_s=30.0)
    update_run(conn, run_id, state="running")
    watcher.poll_once()
    assert "worker_lost" not in kinds(posted)
    now[0] += 31
    watcher.poll_once()
    watcher.poll_once()
    assert kinds(posted).count("worker_lost") == 1


def test_failed_run_ends_the_watch(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="failed", needs_attention="worker failed: boom")
    watcher.poll_once()
    assert posted[-1].kind == "run_done" and posted[-1].data["needs_attention"] == "worker failed: boom"


def test_thread_start_and_stop(calc_repo):
    paths, run_id, conn, events, watcher, posted, _ = setup(calc_repo, interval_s=0.01)
    watcher.start()
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="completed")
    watcher._thread.join(timeout=5)  # the thread ends by itself after run_done
    assert watcher.done
    watcher.stop()
```

- [ ] **Step 2: Run to verify failure** — FAIL (`ModuleNotFoundError: phil.chat.watcher`).

- [ ] **Step 3: Implement** — `src/phil/chat/watcher.py`:

```python
import threading
import time
from collections.abc import Callable
from datetime import datetime

from phil.chat.events import ChatEvent
from phil.run.launch import is_worker_alive, worker_starting
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.store.telemetry import run_totals

ENDED = ("completed", "aborted", "cleaned", "failed", "stopped")


class RunWatcher:
    """Follows one run for its chat: tails the run's event log and row, posts what changed."""

    def __init__(
        self,
        paths: ProjectPaths,
        run_id: str,
        post: Callable[[ChatEvent], None],
        *,
        alive=is_worker_alive,
        starting=worker_starting,
        clock: Callable[[], float] = time.time,
        lost_after_s: float = 30.0,
        interval_s: float = 1.0,
    ) -> None:
        self.paths, self.run_id, self.post = paths, run_id, post
        self.alive, self.starting, self.clock = alive, starting, clock
        self.lost_after_s, self.interval_s = lost_after_s, interval_s
        self.events = run_events(paths, run_id)
        self.done = False
        self._last: tuple | None = None
        self._paused_ts: str | None = None
        self._paused = False
        self._idle_since: float | None = None
        self._lost_posted = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> None:
        if self.done:
            return
        conn = connect(self.paths.db_path)
        try:
            record = get_run(conn, self.run_id)
            if record is None:
                return
            snapshot = (record.current_node, record.state, record.tasks_done, record.tasks_total)
            if snapshot != self._last:
                self._last = snapshot
                started = datetime.fromisoformat(record.created_at).timestamp()
                self.post(ChatEvent("run_progress", {
                    "node": record.current_node, "state": record.state, "tasks_done": record.tasks_done,
                    "tasks_total": record.tasks_total, "keyword": record.keyword, "started": started,
                }))
            active = self.alive(record) or self.starting(self.events)
            if record.state == "escalated":
                latest = self.events.latest("escalation")
                if latest and not active and latest.get("ts") != self._paused_ts:
                    self._paused_ts, self._paused = latest.get("ts"), True
                    self.post(ChatEvent("run_paused", {"escalation": latest["escalation"]}))
            elif self._paused:
                self._paused = False
                self.post(ChatEvent("run_resumed", {}))
            if record.state in ENDED:
                tokens, cost = run_totals(conn, self.run_id)
                self.done = True
                self.post(ChatEvent("run_done", {
                    "state": record.state, "tasks_done": record.tasks_done, "tasks_total": record.tasks_total,
                    "needs_attention": record.needs_attention, "tokens": tokens, "cost_usd": cost,
                    "summary": str(self.paths.run_dir(self.run_id) / "summary.md"),
                }))
                return
            if record.state in ("running", "pending") and not active:
                now = self.clock()
                self._idle_since = self._idle_since or now
                if now - self._idle_since > self.lost_after_s and not self._lost_posted:
                    self._lost_posted = True
                    self.post(ChatEvent("worker_lost", {}))
            else:
                self._idle_since, self._lost_posted = None, False
        finally:
            conn.close()

    def _run(self) -> None:
        while not self._stop.is_set() and not self.done:
            try:
                self.poll_once()
            except Exception:  # a transient read error must not kill the watch
                pass
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"watch-{self.run_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
```

(Check `run_totals` and `worker_starting` signatures in the codebase; if `created_at` parsing differs, follow the stored format. `poll_once` reads the row every call; the event log only matters for escalation payloads, so no offset is needed.)

- [ ] **Step 4: Run tests** — `uv run pytest -q tests/chat/test_watcher.py`, then the full suite → PASS.

- [ ] **Step 5: Commit** — `Add the run watcher for chats`.

---

### Task 5: `/btw` — contract, agent, answer view

**Files:** Modify `src/phil/contracts/inputs.py`, `src/phil/contracts/__init__.py`, `src/phil/agents/registry.py`, `tests/agents/test_registry.py`; Create `src/phil/prompts/btw.md`, `src/phil/chat/btw.py`, `src/phil/ui/brief_view.py`, `tests/chat/test_btw.py`, `tests/ui/test_brief_view.py`.

**Interfaces — Produces:**
- `BtwInput(question: str, goal: Goal | None = None, plan: Plan | None = None, run: RunStatus | None = None, recent_events: list[str] = [], pending_question: str | None = None)`.
- Spec `"btw": AgentSpec("btw", "orchestrator", BtwInput, Brief)` — deep harness, `writes_files=False` (read-only file tools on the snapshot).
- `ask_btw(ctx: AgentContext, question: str, *, goal=None, plan=None, run=None, recent_events=(), pending_question=None, tree: Path | None = None, call: int = 1) -> Brief` — builds the packet with the `orchestrator` budget and invokes with `node="btw"`; the context gets `workdir=tree` (via `dataclasses.replace`) only when a tree is given.
- `render_brief(console, brief: Brief) -> None` — `headline` (brand style), optional `status` (muted), up to 5 `points` as bullets, `needs_you` decisions as `? question [opt / opt]`, `details` as `→ label: path` (muted); all escaped.
- `tests/agents/test_registry.py`: add `"btw"` to the registered-names assertion.

- [ ] **Step 1: Failing tests**

`tests/chat/test_btw.py`:

```python
from phil.agents.fake import ScriptedAgentFactory
from phil.chat.btw import ask_btw
from phil.contracts import Brief, RunStatus
from tests.chat.conftest import goal, plan


def test_btw_passes_context_and_uses_the_snapshot(chat_ctx, tmp_path):
    seen = {}

    def answer(turn):
        seen["workdir"] = turn.workdir
        seen["payload"] = str(turn.payload)
        return Brief(headline="CALC-002 is in the green phase", points=["tests ran twice"])

    factory = ScriptedAgentFactory({"btw": [answer]})
    run = RunStatus(run_id="r-1", state="running", tasks_done=1, tasks_total=2, current_node="implement")
    brief = ask_btw(
        chat_ctx(factory), "why is it slow?", goal=goal(), plan=plan(), run=run,
        recent_events=["node implement"], pending_question=None, tree=tmp_path,
    )
    assert brief.headline == "CALC-002 is in the green phase"
    assert seen["workdir"] == tmp_path
    assert "why is it slow?" in seen["payload"] and "r-1" in seen["payload"]


def test_btw_without_a_snapshot_has_no_workdir(chat_ctx):
    factory = ScriptedAgentFactory({"btw": [lambda turn: Brief(headline=str(turn.workdir))]})
    assert ask_btw(chat_ctx(factory), "hi").headline == "None"
```

`tests/ui/test_brief_view.py`:

```python
from phil.contracts import Brief, Decision, Ref
from phil.ui.brief_view import render_brief
from phil.ui.theme import make_console


def test_render_brief():
    console = make_console(record=True, width=120)
    render_brief(console, Brief(
        headline="Auth is in [bold]auth.py[/bold]", status="read 3 files", points=["login()", "logout()"],
        needs_you=[Decision(question="Split it?", options=["yes", "no"])], details=[Ref(label="auth", path="src/auth.py")],
    ))
    text = console.export_text()
    assert "Auth is in [bold]auth.py[/bold]" in text
    assert "• login()" in text and "? Split it? [yes / no]" in text and "→ auth: src/auth.py" in text
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

`src/phil/contracts/inputs.py` — import `Brief`/`RunStatus` from `phil.contracts.interface`; add:

```python
class BtwInput(Contract):
    question: str
    goal: Goal | None = None
    plan: Plan | None = None
    run: RunStatus | None = None
    recent_events: list[str] = []
    pending_question: str | None = None
```

Export `BtwInput` from `phil.contracts` (import list, `__all__`, and `ALL_CONTRACTS` if that list exists). Registry: `"btw": AgentSpec("btw", "orchestrator", BtwInput, Brief)`.

`src/phil/prompts/btw.md`:

```markdown
# Role: Side questions (/btw)

The user asks a quick question while Phil works on their goal. Answer it; do not change anything.

## What you can use
- `goal`, `plan`: what this chat is working on.
- `run`, `recent_events`, `pending_question`: the state of the chat's background run, if any.
- Read-only file tools on a snapshot of the repo at the goal's base commit. Read only what you need.

## Answer
- Return a `Brief`: `headline` answers the question in one line; `points` add at most 5 short facts; `details` point to files (`label`, `path`).
- If the question asks you to change the plan or the run, say how the user can do it (answer the pending question, `edit` the plan, start a new goal) instead of doing it.
- Say plainly when you don't know. Never invent run results or file contents.
```

`src/phil/chat/btw.py`:

```python
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import Brief, BtwInput, Goal, Plan, RunStatus
from phil.packets import build_packet


def ask_btw(
    ctx: AgentContext,
    question: str,
    *,
    goal: Goal | None = None,
    plan: Plan | None = None,
    run: RunStatus | None = None,
    recent_events: Sequence[str] = (),
    pending_question: str | None = None,
    tree: Path | None = None,
    call: int = 1,
) -> Brief:
    contract = BtwInput(
        question=question, goal=goal, plan=plan, run=run,
        recent_events=list(recent_events), pending_question=pending_question,
    )
    packet = build_packet("orchestrator", contract, budget_tokens=ctx.config.budget_for("orchestrator").max_input_tokens)
    btw_ctx = replace(ctx, workdir=tree) if tree is not None else ctx
    return invoke_agent(get_spec("btw"), packet, btw_ctx, node="btw", call=call)
```

`src/phil/ui/brief_view.py`:

```python
from rich.console import Console
from rich.markup import escape

from phil.contracts import Brief


def render_brief(console: Console, brief: Brief) -> None:
    console.print(f"[phil.brand]{escape(brief.headline)}[/]")
    if brief.status:
        console.print(f"[phil.muted]{escape(brief.status)}[/]")
    for point in brief.points:
        console.print(f"  • {escape(point)}")
    for decision in brief.needs_you:
        console.print(f"  [phil.warn]? {escape(decision.question)} [{escape(' / '.join(decision.options))}][/]")
    for ref in brief.details:
        console.print(f"  [phil.muted]→ {escape(ref.label)}: {escape(ref.path)}[/]")
```

(`[{escape(...)}]` inside markup: the literal `[` before the options must itself be escaped — use `\\[` or build the bracket text with `escape("[" + ... + "]")`; the test's expected text `? Split it? [yes / no]` guards this.)

- [ ] **Step 4: Run tests** — `uv run pytest -q tests/chat tests/ui tests/agents`, then the full suite → PASS.

- [ ] **Step 5: Commit** — `Add /btw side questions: contract, agent, and answer view`.

---

### Task 6: The staged, event-driven chat controller (goal → plan → approval)

**Files:** Rewrite `src/phil/chat/controller.py`; Modify `tests/chat/test_controller.py`, `tests/chat/conftest.py`; keep `src/phil/cli/main.py` working (it constructs `ChatController` + `ChatIO`).

**Interfaces — Produces** (replacing 4a's blocking controller; existing behaviour of every 4a test is preserved unless noted):

```python
WAKE = object()  # ChatIO.ask returns this when a background event interrupted the prompt

@dataclass
class ChatIO:
    ask: Callable[[str], object]                       # prompt -> str | None (EOF) | WAKE; may raise KeyboardInterrupt
    spawn: Callable[[Path, str, str, dict | None], object]   # (repo_root, run_id, mode, decision)
    wake: Callable[[], None] = lambda: None             # interrupt a blocked ask (keeps typed text)
    submit: Callable[[Callable[[], None]], object] = lambda job: job()   # run a job; inline by default
```

`ChatController(info, config, conn, console, io, *, factory=None, base_sha=None, session=None, sleep=None, watcher_factory=None)`; `run()`; `state: ChatState`; `post(event)` (thread-safe: queue put + `io.wake()`).

Stages and the prompt each shows:

| stage | prompt | non-slash input does |
|---|---|---|
| `idle` | `you › ` | starts a goal |
| `intake`, `planning` (a goal job in flight) | `you › ` | asks `Replace the current goal? [y / n] › ` (sub-stage `confirm_replace`) |
| `questions` | `answers (or 'go' to plan anyway) › ` | `go` → plan; else intake again with the answer |
| `approval` | `Approve? [y / edit / n] › ` | 4a approval rules |
| `edit` | `What should change? › ` | empty → back to approval; else a revise job |
| `running` | `you › ` | refused: `This chat is following run <id>. Use /btw to ask about it, or start another goal in a new window.` |

Rules:
- The loop, each turn: drain the queue (handle events, print), then `io.ask(prompt)`. `WAKE` → loop again. `None` (EOF) → end (Task 7 adds the "run keeps working" hint). A slash command is handled in every stage. Record every raw input in the transcript with its stage before stripping (4a rule).
- **Jobs:** `_job(kind, fn)` captures the current goal generation; runs `fn` via `io.submit`; posts `ChatEvent(kind, result, generation)` or `ChatEvent("job_failed", {"job": kind, "error": "<Type>: <msg>"}, generation)`; always `state.set_step(None, now)` at the end. Events with a stale generation are ignored (and clear `cancelling`).
- **Goal flow:** `_begin_goal(text)`: generation += 1, stage `intake`, job `goal_ready` = intake(...) (step `intake`). On `goal_ready`: record the goal; if it has open questions and rounds < 2 → print them numbered (clipped via the plan view's clip helper), stage `questions`; else `_plan(goal)`. `_plan`: note open questions if any (`Planning with open questions: N`), `render_goal`, stage `planning`, job `plan_ready` = `planner.draft(goal, snapshot, on_step=…)` with step `snapshot` while exporting. On `plan_ready`: record plan + critique, stage `approval`, render the plan (4a view). `edit` feedback → stage `planning`, job `plan_ready` = `planner.revise(...)`.
- **Approval `y`:** 4a behaviour (re-read config, `launch_problems`, `_require`-style key check is CLI-only), then `_start`: `prepare_run(..., chat_id=session.id)`, spawn `(root, run_id, "start", None)`; stage `running` (Task 7 starts the watcher). Spawn failure: 4a message with `phil resume <id>`, stage back to `idle`.
- **`job_failed`:** print `Phil couldn't finish that: <error>` + `Details: <session dir>`; stage → `approval` if the failed job was a revise (keep the old draft), else `idle`.
- **Ctrl-C** (`KeyboardInterrupt` from `ask`): in `intake`/`planning` → generation += 1, `state.set_cancelling(True)`, stage `idle`, print `Cancelled the current goal.`; in `edit`/`confirm_replace` → back to the parent stage; else print `Cancelled.`.
- **Replace:** `confirm_replace` `y` → `_begin_goal(new text)` (old results become stale); anything else → `Keeping the current goal.`, back to the previous stage.
- **Commands:** `/help`, `/runs`, `/quit`/`/exit` (end), unknown → `Unknown command /x. Try /help.` (Task 7 adds `/btw`, `/answer`, `/resume`.) `HELP` text lists the commands available.
- `ChatState` stage/step are updated on every change so the toolbar is right.
- `_save()` writes `state.json` after every stage change: `{"stage", "goal": goal.model_dump(mode="json") | None, "plan", "critique", "version", "run_id", "base_sha", "done_seen"}` (via `_safe_note`-style guard: a failed write prints nothing and never raises).

- [ ] **Step 1: Failing tests** — update `tests/chat/test_controller.py`:
  - Replace the harness with a scripted IO that supports callables: each script item is a string, `None`, or a callable `(controller) -> str | None | WAKE`; `submit` is inline; `spawn` records `(run_id, mode, decision)`.

```python
def run_chat(repo, answers, scripts, config=None, **kw):
    info = resolve_repo(repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = list(answers)
    spawned = []
    holder = {}

    def ask(prompt):
        holder.setdefault("prompts", []).append(prompt)
        if not queue:
            return None
        item = queue.pop(0)
        return item(holder["controller"]) if callable(item) else item

    io = ChatIO(ask=ask, spawn=lambda root, run_id, mode, decision=None: spawned.append((run_id, mode, decision)))
    factory = ScriptedAgentFactory(scripts)
    controller = ChatController(info, config or PhilConfig(models=TEST_MODELS), conn, console, io, factory=factory, **kw)
    holder["controller"] = controller
    controller.run()
    return console.export_text(), spawned, list_runs(conn), factory, holder["prompts"]
```

  - Update every existing test to the new return shape (`text, spawned, runs, factory, prompts = run_chat(...)`) and to `spawned` entries `(run_id, mode, decision)`; keep their assertions.
  - New tests:

```python
def test_prompts_follow_the_stage(calc_repo):
    *_, prompts = run_chat(calc_repo, ["add subtract", "y"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    assert prompts[:2] == ["you › ", "Approve? [y / edit / n] › "]


def test_runs_are_linked_to_the_chat(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    assert runs[0].chat_id and runs[0].chat_id.startswith("c-")


def test_new_goal_while_running_is_refused(calc_repo):
    text, *_ = run_chat(calc_repo, ["add subtract", "y", "add multiply"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    assert "is following run" in text


def test_replace_goal_mid_planning(calc_repo):
    # A deferred submit keeps the first goal's job pending so a second goal arrives mid-planning.
    jobs = []
    def ask_second(controller):
        return "add multiply"
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", ask_second, "y", "n"],
        {"intake": [goal(), goal("Add multiply")], "architect": [plan()], "critic": [critique()]},
        # run the first goal's job only after the replace was confirmed
    )
    assert "Replace the current goal? [y / n] › " in prompts
```

  (For the replace test, give the harness an optional `submit` override: collect jobs in a list and run them when the script calls `lambda c: (run_pending(), WAKE)[1]`. Write the test so the first goal's `goal_ready` arrives *after* the replacement and is ignored as stale — assert the transcript/goal shown is "Add multiply" and the scripted intake for "add subtract" was consumed but its plan never rendered. Adjust the sketch above into a precise, deterministic test.)

```python
def test_ctrl_c_during_planning_cancels_the_goal(calc_repo):
    ...  # deferred submit; script raises KeyboardInterrupt while the job is pending; then run the job; assert
         # "Cancelled the current goal." printed and no plan rendered; next prompt is "you › ".


def test_state_json_tracks_the_stage(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    info = resolve_repo(calc_repo)
    [session_dir] = (ProjectPaths(info.slug).project_dir / "chats").iterdir()
    state = json.loads((session_dir / "state.json").read_text())
    assert state["stage"] == "running" and state["run_id"] == runs[0].run_id and state["plan"]["keyword"] == "CALC"
```

  Implement the two `...` tests fully (the deferred-submit helper: `ChatIO(submit=pending.append, ...)` and a script item `lambda c: (pending.pop(0)(), WAKE)[1]`).

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** — rewrite `src/phil/chat/controller.py` to the interfaces above. Structure (fill in bodies following the rules; keep 4a helpers — `_snapshot`, `_remove_snapshots`, `_safe_note`, `_record_draft`, the approval block, `_start` — adapted to stages):

```python
import queue
import shutil
import time
# ... 4a imports ...
from phil.chat.events import ChatEvent
from phil.chat.state import ChatState

WAKE = object()
MAX_QUESTION_ROUNDS = 2
HELP = "Type a goal to plan it. Commands: /btw <question>, /answer, /runs, /help, /quit (or Ctrl-D)."
PROMPTS = {
    "questions": "answers (or 'go' to plan anyway) › ",
    "approval": "Approve? [y / edit / n] › ",
    "edit": "What should change? › ",
    "confirm_replace": "Replace the current goal? [y / n] › ",
}


class ChatController:
    def __init__(self, info, config, conn, console, io, *, factory=None, base_sha=None, session=None,
                 sleep=None, watcher_factory=None) -> None:
        ...  # 4a fields
        self.state = ChatState()
        self.events: queue.Queue[ChatEvent] = queue.Queue()
        self.stage = "idle"
        self._parent_stage = "idle"      # where edit / confirm_replace return to
        self._generation = 0
        self._goal: Goal | None = None
        self._goal_text = ""
        self._rounds = 0
        self._draft = None
        self._run_id: str | None = None
        self._replacement = ""
        self._watcher_factory = watcher_factory   # used in Task 7

    def post(self, event: ChatEvent) -> None:
        self.events.put(event)
        self.io.wake()

    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        self.state.set_stage(stage)
        self._save()

    def _prompt(self) -> str:
        return PROMPTS.get(self.stage, "you › ")

    def run(self) -> None:
        try:
            self._loop()
        finally:
            self._remove_snapshots()
            self._save()

    def _loop(self) -> None:
        while True:
            self._drain()
            try:
                raw = self.io.ask(self._prompt())
            except KeyboardInterrupt:
                self._interrupt()
                continue
            if raw is WAKE:
                continue
            if raw is None:
                return
            if not self._input(raw):
                return

    def _drain(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle(event)
            except Exception as exc:
                self._report(exc)

    def _handle(self, event: ChatEvent) -> None:
        if event.generation not in (-1, self._generation):
            self.state.set_cancelling(False)
            return
        handler = getattr(self, f"_on_{event.kind}", None)
        if handler:
            handler(event.data)

    def _job(self, kind: str, fn) -> None:
        generation = self._generation
        def work() -> None:
            try:
                self.post(ChatEvent(kind, fn(), generation))
            except Exception as exc:
                self.post(ChatEvent("job_failed", {"job": kind, "error": f"{type(exc).__name__}: {exc}"}, generation))
            finally:
                self.state.set_step(None, time.time())
        self.io.submit(work)

    # _input(raw) -> bool: record raw with the stage, route slash commands, then dispatch by stage.
    # _begin_goal, _on_goal_ready, _plan, _on_plan_ready, _approval(text), _edit(text), _confirm_replace(text),
    # _on_job_failed, _interrupt, _start, _save, _report — per the rules above.
```

Update `src/phil/cli/main.py` only as far as needed to keep the current (non-live) chat working: its `ChatIO(ask=..., spawn=lambda root, run_id, mode, decision=None: spawn_worker(root, run_id, mode, decision))` (inline `submit`, no-op `wake`). The live terminal comes in Task 8.

- [ ] **Step 4: Run tests** — `uv run pytest -q tests/chat tests/cli`, then the full suite → PASS.

- [ ] **Step 5: Commit** — `Make the chat controller staged and event-driven`.

---

### Task 7: Follow the run in the chat — progress, pauses, completion, `/btw`, reopening

**Files:** Modify `src/phil/chat/controller.py`, `tests/chat/test_controller.py`; Create `tests/chat/test_controller_run.py`.

**Interfaces — Produces:**
- `watcher_factory: Callable[[str], RunWatcher-like] | None` (default: `lambda run_id: RunWatcher(ProjectPaths(info.slug), run_id, self.post)`); the controller calls `.start()` after a run starts and `.stop()` on exit or when the run ends.
- Event handlers:
  - `_on_run_progress`: update `state.set_run(RunView(...))`; no printing.
  - `_on_run_paused(data)`: store the escalation; `state.set_paused(True)`; print `⏸ <run_id> needs you: <summary>` (warn); stage `paused`. The paused prompt is `<summary> — <opt1 / opt2 / …> › `; answering validates against `options` (`Answer one of: …` otherwise); `retry` → sub-stage `hint` (`Hint for the retry (optional) › `); then `_resume_run(decision)`.
  - `_resume_run(decision)`: re-read the row; if it's no longer `escalated`, or a worker is alive/starting → print `The run moved on; nothing to answer.` and stage `running`; else `io.spawn(root, run_id, "resume", decision)`, clear the pause, stage `running`, `state.set_paused(False)`.
  - `_on_run_resumed`: clear any pending pause silently if the stage is `paused`/`hint` (answered elsewhere) — print muted `<run_id> resumed.` and stage `running`.
  - `_on_run_done(data)`: stop the watcher; `state.set_run(None)`, `set_paused(False)`; print the completion notice:
    - completed: `✓ Run <id> completed · <done>/<total> tasks · <tokens> tokens · $<cost>` then `Review it: phil diff <id>` and `Summary: <path>` (muted); open issues come from `needs_attention` if set.
    - aborted: `Run <id> was aborted.` + summary path.
    - failed/stopped: `Run <id> <state>: <needs_attention>` + `Continue it with /resume.`
    - then mark `done_seen` (unless failed/stopped), clear `_run_id` for completed/aborted, stage `idle` (failed/stopped keep `_run_id` for `/resume`, stage `idle`).
  - `_on_worker_lost`: print `The worker for <id> stopped responding. Continue it with /resume.`
- Commands:
  - `/btw <question>` (empty → `Usage: /btw <question>`): `state.add_btw(1)`; gather context on the main thread (goal, current plan, `RunStatus` from the row + `run_totals` when there is a run, the last 10 run events as short strings like `"node implement"`, `"escalation: <summary>"`, the pending pause summary); submit a job with `generation=-1` whose result is `{"brief": ask_btw(..., tree=<snapshot of the base if a goal exists, else None>)}`; step `btw`. `_on_btw_answer`: `state.add_btw(-1)`, print `btw ›` (muted) then `render_brief`. Failure → `/btw failed: <error>` and `add_btw(-1)` (handle via a dedicated failure event kind `btw_failed` so it doesn't disturb the goal stage).
  - `/answer`: if a pause is pending → stage `paused` (the next prompt asks it); else `Nothing needs you right now.`
  - `/resume`: if the chat's run is `failed`/`stopped` (or `worker_lost` was seen) and no worker is alive/starting → `io.spawn(root, run_id, "continue", None)`, restart the watcher, stage `running`; else `Nothing to resume.`
- EOF / `/quit` while a run is in progress → print `Run <id> keeps working. Reopen this chat with \`phil --resume <chat_id>\`.` before ending; stop the watcher (it's a daemon thread; the worker is independent).
- **Reopen:** `ChatController(..., session=<opened session>, resume=True)`: restore goal/plan/critique/version/run_id/base_sha from `state.json`; print `Reopened <chat_id>: <objective>`; if `run_id` → start the watcher (its first poll posts progress, a pending pause, or completion) and stage `running`; elif stage was `approval` with a plan → rebuild the `PlanDraft` and stage `approval` (plan re-rendered); else stage `idle` with `The chat was closed before a plan was ready; send the goal again or type a new one.`

- [ ] **Step 1: Failing tests** — `tests/chat/test_controller_run.py`. Use the Task 6 harness plus a fake watcher factory that returns a real `RunWatcher` with `alive=lambda r: False`, `starting=lambda e: False` but **not started** (store it so script callables can drive `poll_once()`):

```python
def test_pause_is_answered_in_the_chat(calc_repo):
    # approve → run started; script: write an escalation into the run's event log + row, poll, WAKE;
    # the next prompt is the pause question; answer "retry" then hint "use minus" → spawn resume with that decision.
    ...
    assert ("resume", {"action": "retry", "hint": "use minus"}) in [(m, d) for _, m, d in spawned]
    assert "⏸" in text and "needs you" in text


def test_pause_answered_elsewhere_is_dropped(calc_repo): ...
def test_completion_notice_then_next_goal(calc_repo): ...   # row → completed; poll; notice has "phil diff <id>"; next prompt "you › " accepts a new goal
def test_failed_run_offers_resume(calc_repo): ...           # row → failed; poll; "/resume" spawns (run_id, "continue", None)
def test_btw_answers_during_planning(calc_repo): ...        # deferred submit keeps planning pending; "/btw where is add?" runs inline via a second path; Brief headline printed; plan still arrives afterwards
def test_eof_while_running_prints_the_reopen_hint(calc_repo): ...
def test_reopen_follows_the_run(calc_repo): ...             # first chat approves and ends (EOF); second ChatController(session=ChatSession.open(...), resume=True) polls and shows progress/pause
def test_reopen_at_approval_re_renders_the_plan(calc_repo): ...
```

Write each `...` test fully and deterministically (drive `watcher.poll_once()` from script callables; for `/btw` during planning, use a submit that defers only goal jobs — e.g. inspect a tag you attach to the job function, or keep `submit` inline and make the planning job itself pending via a scripted architect callable that raises nothing but is queued; choose the simplest mechanism and document it in the test).

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement** — per the interfaces above in `src/phil/chat/controller.py`. Keep all output on the main thread (handlers run from `_drain`).

- [ ] **Step 4: Run tests** — `uv run pytest -q tests/chat`, then the full suite → PASS.

- [ ] **Step 5: Commit** — `Follow the chat's run: progress, pauses, completion, /btw, reopening`.

---

### Task 8: The live terminal — `prompt_toolkit` IO and CLI wiring

**Files:** `uv add prompt-toolkit`; Create `src/phil/chat/terminal.py`, `tests/chat/test_terminal.py`; Modify `src/phil/cli/main.py`, `tests/cli/test_chat_command.py`.

**Interfaces — Produces:**
- `LineIO(console)` → a `ChatIO` for non-TTY use: `ask` via `console.input` (EOF → `None`), inline `submit`, no-op `wake` (the 4a behaviour).
- `TerminalIO(toolbar: Callable[[], str], *, input=None, output=None)` → builds a `prompt_toolkit.PromptSession(bottom_toolbar=toolbar, refresh_interval=0.5)` and exposes `chat_io(spawn) -> ChatIO` with:
  - `ask(prompt)`: `session.prompt(prompt, default=<carried text>)`; `EOFError` → `None`; returns `WAKE` when woken.
  - `wake()`: thread-safe — if the prompt app is running, schedule on its event loop (`app.loop.call_soon_threadsafe`) a callback that stores `app.current_buffer.text` as the carried text and calls `app.exit(result=WAKE)`; no-op when no prompt is active (the event is drained before the next prompt anyway). Verify the exact `prompt_toolkit` API (Application `loop` attribute / `is_running`) with the installed version and adapt; keep the behaviour.
  - `submit(job)`: a `ThreadPoolExecutor(max_workers=3, thread_name_prefix="phil-chat")`; `close()` shuts it down with `cancel_futures=True, wait=False`.
  - `run(fn)`: runs `fn()` inside `with patch_stdout(raw=True):` so Rich output from the main thread prints above the prompt.
- `prompt_toolkit` is imported only inside `phil.chat.terminal` (not at `phil.cli.main` module level).
- CLI (`_chat`):
  - `--resume <chat>` (root option): `ChatSession.open` (unknown → escaped error, exit 1) and `ChatController(..., session=…, resume=True)`.
  - `--new`: skip the open-chat list.
  - Otherwise, when stdin and stdout are TTYs and `list_open_chats` is non-empty: print them numbered (`1. c-… · <objective> · run r-… <state>` or `· plan waiting for approval`) and ask `Reopen one? [number / Enter for a new chat] › `; a valid number reopens, Enter starts new.
  - TTY → `TerminalIO` with `toolbar=lambda: render_toolbar(controller.state.view(), time.time())` and a console made with `force_terminal=True` (verify colours survive `patch_stdout`; if not, fall back to the spec's ANSI adapter and note it). Non-TTY → `LineIO`.
  - `spawn` passes the decision: `spawn_worker(root, run_id, mode, decision)`.
- Root help: `--resume` "Reopen a chat by id (see the list shown by `phil`)."; `--new` "Start a new chat without listing open ones."

- [ ] **Step 1: Failing tests**
  - `tests/chat/test_terminal.py`: a smoke test with `prompt_toolkit.input.create_pipe_input()` and `prompt_toolkit.output.DummyOutput()` — build `TerminalIO(lambda: "toolbar text", input=pipe, output=DummyOutput())`, send `"hello\n"`, assert `ask("you › ") == "hello"`; then from another thread call `wake()` while `ask` blocks (send nothing) and assert it returns `WAKE`; send `"\x04"` (Ctrl-D) → `None`; `close()`.
  - `tests/cli/test_chat_command.py`: `phil --resume c-nope` exits 1 with the chat id in the message; `phil --resume <id>` of a chat created by a previous scripted chat (use the existing chat scenario env vars) reopens it (output contains `Reopened <id>`); `phil --new` with an open chat present doesn't list it. The CliRunner is not a TTY, so the list itself is covered by a unit test of the listing function or a small injectable `isatty`.
- [ ] **Step 2: Run to verify failure** — FAIL.
- [ ] **Step 3: Implement** — as specified; keep `phil.cli.main` imports lazy for `phil.chat.*` and `prompt_toolkit`.
- [ ] **Step 4: Run tests** — `uv run pytest -q tests/chat tests/cli tests/test_cli.py`, then the full suite → PASS. Then a manual check in a real terminal (`uv run phil` in `~/Code/phil-scratch` with scripted agents is not possible; document in the report that the live check is the user's).
- [ ] **Step 5: Commit** — `Run the chat in a live terminal with a status toolbar`.

---

### Task 9: Docs

**Files:** Modify `docs/superpowers/specs/2026-09-23-phil-v1-design.md` (§3 example session and §6: point to the 4b design for the live chat, one goal per chat, reopening), `README.md` (usage: toolbar, `/btw`, `/answer`, `/resume`, `phil --resume`, `phil --new`), `docs/superpowers/plans/2026-09-24-phil-04a-followups.md` (mark the chat-ergonomics items done in 4b; list 4c: token/cost accuracy, tool-call telemetry, OpenRouter timeout and 200-with-error, `phil show` / `/more`, `/park`).

- [ ] **Step 1:** Make the edits; keep them short and accurate to the code (commands, prompts, flags).
- [ ] **Step 2:** `uv run pytest -q` (nothing should change) → PASS.
- [ ] **Step 3: Commit** — `Document the live chat`.

---

## Spec coverage

| Spec (4b design) | Task |
|---|---|
| §2 one chat = one goal; next goal after completion | 6, 7 |
| §2 prompt_toolkit + worker threads + event queue | 6 (queue, jobs), 8 (terminal) |
| §2 toolbar | 2 (state), 3 (renderer), 8 (wiring) |
| §2 pauses: flag now, ask when idle, `/answer` | 4, 7 |
| §2 `/btw` read-only with repo snapshot | 5, 7 |
| §2 reopening (`phil` list, `--resume`, `--new`) | 2, 7, 8 |
| §3 `ChatState`, `ChatEvent`, `RunWatcher`, `chat_id`, `state.json` | 1, 2, 4, 6 |
| §4 flows (goal, run, pause, done, `/btw`, reopen) | 6, 7 |
| §5 errors (job failure, Ctrl-C/Ctrl-D, watcher resilience, answered elsewhere, write failures, no TTY) | 4, 6, 7, 8 |
| §6 testing | every task; smoke test in 8 |
| Output adapter | replaced by `patch_stdout(raw=True)` (deviation noted above; verified in 8) |
