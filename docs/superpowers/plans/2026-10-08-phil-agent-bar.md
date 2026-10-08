# Active Agents and Feed Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The live row shows every active agent: the main agent, its sub-agents indented under it, and `/btw` questions. `/feed <agent>` filters the feed's tool lines.

**Architecture:**
- **Tagging.** The activity callback records the parent of each LangChain run, so a tool call nested in an open `task` call is tagged `sub_id` and `sub`.
- **Tracking.** The watcher keeps every open call and posts `live_agents`. The controller keeps sub-agents and `/btw` jobs in `ChatState`.
- **Rendering.** A multi-line live row renderer returns prompt_toolkit fragments.
- **Filtering.** `/feed` filters records before they're rendered.

**Tech Stack:** Python 3.14, LangChain callbacks, prompt_toolkit formatted text, Rich `cell_len`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-phil-agent-bar-design.md`

**Base:** branch `plan-agent-bar`, stacked on `status-bar` (PR #32). Rebase onto `main` once #32 merges.

## Global Constraints

- **Editing and committing:**
  - Edit files only with the Edit and Write tools. Never edit through python, perl, sed, heredocs (including empty ones) or printf in Bash.
  - To commit, write the message to a file with Write, then run `git commit -F <file>`.
  - Every message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, whatever model you are.
- **Safety:**
  - Keep the words "keychain" and "credentials" out of Bash command lines.
  - Never work around a hook or guard. Report BLOCKED.
  - Never read or print `.env` files.
- **Tests:**
  - Never run `-m live` or `-m bench`.
  - Iterate with `uv run pytest <paths> -q -n 0`. Run the full `uv run pytest -q` once at the end of each task, and wait for it before committing.
  - Tests must be portable, and test output must stay clean.
- **Text I/O:** `encoding="utf-8"`, and writes use `newline="\n"`.
- **Recording never fails a run or a tool call**, and rendering never raises into the prompt.
- **No markup injection:** agent, question and tool text is plain text.
- **Constants:**

| Name | Value |
|---|---|
| `MAX_LIVE_LINES` | `3` |
| `FEED_AGENTS` | `("implementer", "tester", "reviewer", "architect", "sub-agent", "engine")` |

- **Copy, verbatim:**
  - `Showing only the {agent}'s lines. /feed to show everything.`
  - `Showing everything again. {n} lines from other agents were hidden: /show {run} to see them.`
  - `Showing everything again.`
  - `Pick one of: implementer, tester, reviewer, architect, sub-agent, engine.`
  - `[feed: {agent}]`
  - `+{n} more`
  - `└ sub-agent · `
  - `/btw · `

## Review Focus

1. **A deep agent whose sub-agent graph passes `parent_run_id` through several chain levels.** The nested tool call is still tagged with its nearest open `task` call. Tested in Task 1.
2. **A `/btw` that fails or is cancelled.** Its live line disappears. Tested in Task 4.
3. **A filter left on when the run ends.** It's cleared, and the next run's feed is unfiltered. Tested in Task 4.
4. **Six concurrent agents on a 40-column terminal.** Four lines (3, then `+3 more`), and none wraps. Tested in Task 2.
5. **An old run's activity without `sub` fields.** It renders and tracks exactly as before. Tested in Task 3.

---

### Task 1: Tag sub-agent tool calls

**Files:**
- Modify:
  - `src/phil/store/activity.py`: `start`, `end` and `record` take an optional `extra: dict | None = None`, merged into the record (never overriding the standard keys).
  - `src/phil/agents/activity.py`: `ActivityCallback` keeps a parent map and tags nested calls.
  - `src/phil/agents/invoke.py`: clear the callback's map when the agent call finishes.
  - `src/phil/agents/fake.py`: `fire_tool` can pass `parent_run_id`, and there's a helper to fire a chain start.
- Test: `tests/agents/test_activity_callback.py`, `tests/store/test_activity.py`

**Interfaces:**
- Produces:
  - Activity records may carry `"sub_id": int` and `"sub": str`.
  - `ActivityCallback.on_chain_start`, `on_chat_model_start` and `on_llm_start`, which record parents.
  - `ActivityCallback.reset()`
  - `fire_tool(turn, name, args, output, *, run_id=None, parent_run_id=None)`
  - `fire_chain(turn, run_id, parent_run_id)`
  - `SUB_TOOL = "task"`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_activity_callback.py (add)
import uuid

from phil.agents.activity import ActivityCallback
from phil.store.activity import ActivityLog


def ids(n):
    return [uuid.uuid4() for _ in range(n)]


def test_a_call_inside_a_task_call_is_tagged_with_it(tmp_path):
    log = ActivityLog(tmp_path)
    cb = ActivityCallback(log, task="T1", role="implementer")
    agent, task_call, sub_chain, sub_model, inner = ids(5)
    cb.on_chain_start({}, {}, run_id=agent)
    cb.on_tool_start({"name": "task"}, "", run_id=task_call, parent_run_id=agent, inputs={"description": "explore tests"})
    cb.on_chain_start({}, {}, run_id=sub_chain, parent_run_id=task_call)
    cb.on_chat_model_start({}, [], run_id=sub_model, parent_run_id=sub_chain)
    cb.on_tool_start({"name": "read_file"}, "", run_id=inner, parent_run_id=sub_chain, inputs={"file_path": "t.py"})
    cb.on_tool_end("x", run_id=inner, parent_run_id=sub_chain)
    cb.on_tool_end("done", run_id=task_call, parent_run_id=agent)
    records, _ = log.read()
    task_seq = next(r["seq"] for r in records if r["tool"] == "task")
    inner_records = [r for r in records if r["tool"] == "read_file"]
    assert all(r["sub_id"] == task_seq and r["sub"] == "explore tests" for r in inner_records)
    assert all("sub_id" not in r for r in records if r["tool"] == "task")


def test_a_call_outside_any_task_call_is_not_tagged(tmp_path):
    log = ActivityLog(tmp_path)
    cb = ActivityCallback(log, task=None, role="implementer")
    agent, call = ids(2)
    cb.on_chain_start({}, {}, run_id=agent)
    cb.on_tool_start({"name": "read_file"}, "", run_id=call, parent_run_id=agent, inputs={"file_path": "a"})
    cb.on_tool_end("x", run_id=call)
    assert all("sub_id" not in r for r in log.read()[0])


def test_nested_task_calls_credit_the_nearest(tmp_path):
    log = ActivityLog(tmp_path)
    cb = ActivityCallback(log, task=None, role="implementer")
    outer, inner_task, call = ids(3)
    cb.on_tool_start({"name": "task"}, "", run_id=outer, inputs={"description": "outer"})
    cb.on_tool_start({"name": "task"}, "", run_id=inner_task, parent_run_id=outer, inputs={"description": "inner"})
    cb.on_tool_start({"name": "grep"}, "", run_id=call, parent_run_id=inner_task, inputs={"pattern": "x"})
    start = [r for r in log.read()[0] if r["tool"] == "grep"][0]
    assert start["sub"] == "inner"


def test_reset_empties_the_parent_map(tmp_path):
    cb = ActivityCallback(ActivityLog(tmp_path), task=None, role="r")
    a, b = ids(2)
    cb.on_chain_start({}, {}, run_id=b, parent_run_id=a)
    cb.reset()
    assert cb._parents == {} and cb._subs == {}


def test_bookkeeping_never_raises(tmp_path):
    cb = ActivityCallback(ActivityLog(tmp_path), task=None, role="r")
    cb.on_chain_start(None, None, run_id=None, parent_run_id=object())  # nonsense input
    cb.on_llm_start(None, None, run_id=uuid.uuid4())
```

```python
# tests/store/test_activity.py (add)
def test_extra_fields_are_merged_but_never_override(tmp_path):
    log = ActivityLog(tmp_path)
    seq = log.start(task="T", role="r", tool="t", summary="s", extra={"sub_id": 3, "sub": "x", "seq": 99})
    log.end(seq, task="T", role="r", tool="t", summary="s", result="", ok=True, detail=None, duration_ms=1,
            extra={"sub_id": 3, "sub": "x"})
    start, end = log.read()[0]
    assert start["seq"] == seq and start["sub_id"] == 3 and end["sub"] == "x"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_activity_callback.py tests/store/test_activity.py -q -n 0`

- [ ] **Step 3: Implement**

**`ActivityLog`.** Add `extra: dict | None = None` to `start`, `end` and `record`. Merge it as `{**(extra or {}), **standard_record}`, so the standard keys always win. Keep every existing guard.

**`ActivityCallback`:**

```python
SUB_TOOL = "task"
```

In `__init__`:

```python
        self._parents: dict[object, object] = {}  # LangChain run id -> parent run id
        self._subs: dict[object, tuple[int | None, str]] = {}  # an open task call's run id -> (seq, description)
```

```python
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
        with self._lock:
            self._parents.clear()
            self._subs.clear()
```

- **`on_tool_start`:** accept `parent_run_id=None` and call `_note_parent(run_id, parent_run_id)`. Compute `extra = self._sub_for(parent_run_id)` (a `task` call is never tagged as being inside itself, but can be inside another one), and pass `extra=extra or None` to `log.start`. Keep `extra` in `_open[run_id]` so `_finish` can pass it to `log.end`.
- **A `task` call:** after its start is logged, store `self._subs[run_id] = (seq, _first_line(args.get("description", "")) if args else "")`.
- **`_finish`:** pass the stored `extra` to `log.end`, and drop `run_id` from `_subs` and `_parents`.

**`invoke_agent`** calls `activity_callback.reset()` in a `finally` after each attempt's agent call, where the callback exists.

**`fake.py`:**

```python
def fire_tool(turn, name, args, output, *, run_id=None, parent_run_id=None) -> None:
    import uuid

    run_id = run_id or uuid.uuid4()
    for callback in (turn.config or {}).get("callbacks", []):
        callback.on_tool_start({"name": name}, "", run_id=run_id, parent_run_id=parent_run_id, inputs=args)
        callback.on_tool_end(output, run_id=run_id, parent_run_id=parent_run_id)


def fire_chain(turn, run_id, parent_run_id=None) -> None:
    for callback in (turn.config or {}).get("callbacks", []):
        if hasattr(callback, "on_chain_start"):
            callback.on_chain_start({}, {}, run_id=run_id, parent_run_id=parent_run_id)
```

`UsageCollector` subclasses `BaseCallbackHandler`, so `on_chain_start` already exists on it as a no-op, and calling it is safe.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents tests/store -q -n 0`.

- [ ] **Step 5: Run the full suite, then commit**

Run the full suite.
Commit message: `Activity: tag tool calls made inside a sub-agent with its task call`, plus the trailer.

---

### Task 2: The multi-line live row

**Files:**
- Modify:
  - `src/phil/chat/state.py`: `SubAgent`, `SideJob` and the new `ToolbarView` fields.
  - `src/phil/ui/toolbar.py`: `render_live_rows`.
  - `src/phil/ui/theme.py`: `phil.sub` (magenta) in `PHIL_THEME` and in the prompt_toolkit rules.
  - `src/phil/chat/terminal.py`: the live row callable may return fragments.
  - `src/phil/cli/main.py`: the `live_row` closure uses `render_live_rows`.
- Test: `tests/ui/test_toolbar.py`, `tests/chat/test_terminal.py`

**Interfaces:**
- Produces:
  - `SubAgent(seq: int, description: str, summary: str | None, started: float)`, a frozen dataclass.
  - `SideJob(label: str, started: float)`, a frozen dataclass.
  - **`ToolbarView`, new fields:**
    - `subs: tuple[SubAgent, ...] = ()`
    - `side: tuple[SideJob, ...] = ()`
    - `feed_filter: str | None = None`
  - **`ChatState` methods:**
    - `set_subs(subs)`
    - `add_side(job) -> None`
    - `remove_side(job) -> None`
    - `set_feed_filter(agent: str | None)`
  - `render_live_rows(view: ToolbarView, now: float, width: int | None = None) -> list[tuple[str, str]]`: fragments with `"\n"` between lines and none after the last. An empty list means no live row.
  - `MAX_LIVE_LINES = 3`

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_toolbar.py (add)
from phil.chat.state import LiveStep, RunView, SideJob, SubAgent, ToolbarView
from phil.ui.toolbar import render_live_rows, toolbar_text

RUN = RunView(run_id="r-1", keyword="calc", node="implement", tasks_done=0, tasks_total=2, started=0.0)
MAIN = LiveStep(task="CALC-002", role="implementer", summary="run pytest -q", started=98.0)


def lines(view, now=100.0, width=None):
    return toolbar_text(render_live_rows(view, now, width)).split("\n") if render_live_rows(view, now, width) else []


def test_one_agent_is_exactly_todays_row():
    out = lines(ToolbarView(run=RUN, live=MAIN))
    assert len(out) == 1 and out[0][2:] == "CALC-002 · implementer · run pytest -q · 2s"


def test_main_plus_a_sub_agent():
    view = ToolbarView(run=RUN, live=MAIN, subs=(SubAgent(7, "explore tests", "read t.py", 94.0),))
    out = lines(view)
    assert out[1][2:] == " └ sub-agent · read t.py · explore tests · 6s"


def test_a_sub_agent_between_calls_says_working():
    view = ToolbarView(run=RUN, live=MAIN, subs=(SubAgent(7, "explore tests", None, 94.0),))
    assert lines(view)[1][2:] == " └ sub-agent · working · explore tests · 6s"


def test_a_btw_line():
    view = ToolbarView(run=RUN, live=MAIN, side=(SideJob('"why 3 tries?"', 96.0),))
    assert lines(view)[1][2:] == '/btw · "why 3 tries?" · 4s'


def test_more_than_three_collapses_and_nothing_wraps():
    subs = tuple(SubAgent(i, f"job {i}", None, 90.0) for i in range(4))
    view = ToolbarView(run=RUN, live=MAIN, subs=subs, side=(SideJob("q", 99.0),))
    out = lines(view, width=40)
    assert len(out) == 4 and out[-1].strip() == "+3 more"
    assert all(cell_len(line) <= 39 for line in out)


def test_goal_step_without_a_run():
    view = ToolbarView(step="architect", step_started=88.0)
    assert lines(view)[0][2:] == "Architect drafting · 12s"


def test_feed_tag_ends_the_first_line():
    view = ToolbarView(run=RUN, live=MAIN, feed_filter="tester")
    assert lines(view)[0].endswith("[feed: tester]")


def test_spinner_styles():
    view = ToolbarView(run=RUN, live=MAIN, subs=(SubAgent(7, "x", None, 94.0),), side=(SideJob("q", 96.0),))
    frags = render_live_rows(view, 100.0)
    spinner_styles = [s for s, t in frags if t and t[0] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"]
    assert spinner_styles == ["class:phil.agent", "class:phil.sub", "class:phil.warn"]


def test_nothing_running_is_empty():
    assert render_live_rows(ToolbarView(), 0.0) == []
```

Add a terminal test: when the `live_row` callable returns fragments, `_message` puts them above the prompt with a newline after the last one, and an empty list shows no row. Keep the existing string-returning tests passing (strings are still accepted).

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

`state.py`:
- add the two dataclasses and the three fields;
- `add_side` appends, and `remove_side` removes the first equal entry, both under the lock;
- `set_subs(subs: tuple)`;
- `set_feed_filter(agent)`.

`toolbar.py`:

```python
MAX_LIVE_LINES = 3


def _spin(style: str, started: float, now: float) -> tuple[str, str]:
    return (style, SPINNER[int((now - started) * 8) % len(SPINNER)])


def render_live_rows(view: ToolbarView, now: float, width: int | None = None) -> list[tuple[str, str]]:
    rows: list[list[tuple[str, str]]] = []
    if view.run is not None:
        if view.live is not None:
            live = view.live
            parts = [p for p in (live.task, live.role, live.summary, elapsed(now - live.started)) if p]
            rows.append([_spin("class:phil.agent", live.started, now), ("", " " + " · ".join(parts))])
        else:
            node = view.run.node or "starting"
            rows.append([("", "  " + NODE_LABELS.get(node, node))])
        for sub in view.subs:
            what = sub.summary or "working"
            rows.append([_spin("class:phil.sub", sub.started, now),
                         ("", f"  └ sub-agent · {what} · {sub.description} · {elapsed(now - sub.started)}")])
    if view.step:
        label = STEP_LABELS.get(view.step, view.step)
        rows.append([_spin("class:phil.warn" if view.run else "class:phil.agent", view.step_started, now),
                     ("", f" {label} · {elapsed(now - view.step_started)}")])
    for job in view.side:
        rows.append([_spin("class:phil.warn", job.started, now), ("", f" /btw · {job.label} · {elapsed(now - job.started)}")])
    if not rows:
        return []
    if len(rows) > MAX_LIVE_LINES + 1:
        rows = [*rows[:MAX_LIVE_LINES], [("class:phil.muted", f"  +{len(rows) - MAX_LIVE_LINES} more")]]
    if view.feed_filter:
        rows[0] = [*rows[0], ("class:phil.muted", f"   [feed: {view.feed_filter}]")]
    out: list[tuple[str, str]] = []
    for i, row in enumerate(rows):
        if i:
            out.append(("", "\n"))
        out += _fit_row(row, width)
    return out
```

- **`_fit_row(row, width)`:** if the row's text is wider than `width - 1` cells, keep the spinner fragment's style and cut the rest of the text with `_fit`. The `[feed: x]` tag must survive a cut. If that's awkward, cut the body text before the tag, so the tag stays.
- **The step line:** while a run is active, the goal step appears as a side line in yellow. With no run, it's the main line, in cyan.
- **The test strings strip the first 2 cells** (the spinner and a space). Align the code's spacing with them, because the tests are the contract. In particular, `" └ sub-agent"` starts with a space after the spinner's following space.
- **Keep `render_live_row`** (the old single-line str function) only if other callers need it. Otherwise make it a thin wrapper, or remove it and update its tests to the new function, so you don't leave two renderers.

`theme.py`: add `"phil.sub": "magenta"` to `PHIL_THEME`, and include `phil.sub` and `phil.agent` in `prompt_toolkit_styles()`.

`terminal.py`, in `_message` and `_decision_message`:
- if the live row value is a list, use it as fragments, followed by `("", "\n")` when it's non-empty;
- if it's a string, keep the current behaviour.

`main.py`: `live_row()` returns `render_live_rows(controller.state.view(), time.time(), width=terminal.width())`, or `[]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ui tests/chat/test_terminal.py tests/cli -q -n 0`.

- [ ] **Step 5: Run the full suite, then commit**

Commit message: `Live row: one line per active agent`, plus the trailer.

---

### Task 3: The watcher posts every active agent

**Files:**
- Modify:
  - `src/phil/chat/watcher.py`: `_follow_live` builds `main` and `subs` from `_open`, and posts `live_agents`.
  - `src/phil/chat/controller.py`: `_on_live_agents`; `live_agents` added to the accepted run events; `subs` cleared wherever the live step is cleared.
- Test: `tests/chat/test_watcher.py`, `tests/chat/test_controller_feed.py`

**Interfaces:**
- Consumes: `sub_id` and `sub` on records (Task 1); `SubAgent` and `ChatState.set_subs` (Task 2).
- Produces:
  - `ChatEvent("live_agents", {"main": dict | None, "subs": [{"seq", "description", "summary", "started"}, ...]})`, posted when the set changes.
  - `live_step` is still posted with `main`, for compatibility.

- [ ] **Step 1: Write the failing tests**

```python
def test_watcher_posts_main_and_subs(watcher_setup):
    """Open records: a `task` start (seq 5, role implementer, summary 'sub-agent: explore'), an inner
    read_file start with sub_id=5 and sub='explore' (seq 6), and an implementer run_shell start (seq 7,
    no sub_id). One poll posts live_agents with main.seq == 7 and subs == [{seq: 5, description: 'explore',
    summary: 'read <path>', started: <the task start's time>}]. After an end record for 6, subs[0].summary
    is None. After an end for 5, subs == []."""


def test_an_engine_record_is_main_only_when_nothing_else_is_open(watcher_setup):
    """Open: an engine gate start, plus an implementer start: main is the implementer's. After the
    implementer ends, main is the gate."""


def test_spawn_clears_every_agent(watcher_setup):
    """With a sub open, a spawn event read in the next poll posts live_agents with main None and subs []."""


def test_old_records_without_sub_fields_track_as_before(watcher_setup):
    """Records with no sub_id (an old run): live_step and live_agents.main match today's newest-open
    rule, and subs is []."""


def test_controller_keeps_subs_and_clears_them(controller):
    """A live_agents event sets state.view().subs to SubAgent tuples; worker_lost, run_done and
    _forget_run clear them to ()."""
```

**Ruling:** these are specified by docstring. Write them with the existing watcher and controller harnesses, making every assertion stated.

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

**Watcher.** `_follow_live` already maintains `_open` (seq → start record) across polls, including the spawn rule. After it updates `_open`, it computes:

- **`subs`.** For each open record whose `tool == "task"`, a sub-agent entry:
  - `seq`: the record's seq;
  - `description`: the record's summary with its `"sub-agent: "` prefix stripped, or the `sub` field of an inner record;
  - `summary`: the summary of the newest open record with `sub_id == seq`, or None;
  - `started`: the task record's time.

  Inner records that carry a `sub_id` with no open `task` record still count. Use their `sub` as the description and their start time.
- **`main`.** The newest open record with no `sub_id`, whose `tool != "task"` and `role != "engine"`. Otherwise the newest open `engine` record. Otherwise None. It's turned into a dict with `_live_of`.

Post `live_agents` when `(main seq, tuple of (sub seq, its summary))` changes. Keep posting `live_step` exactly as today, but built from `main`.

**Controller.**
- `_on_live_agents(data)`: build `SubAgent`s from `data["subs"]` and call `set_subs`. `main` keeps going through the existing `live_step` path, so don't handle it twice.
- Clear the subs (`set_subs(())`) in `_on_worker_lost` and in the shared run-clearing helper.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat -q -n 0`.

- [ ] **Step 5: Run the full suite, then commit**

Commit message: `Watcher: post every active agent, sub-agents included`, plus the trailer.

---

### Task 4: `/btw` lines, the `/feed` filter, and the end-to-end test

**Files:**
- Modify:
  - `src/phil/chat/controller.py`:
    - `_btw` adds a `SideJob`, and the answer and failure handlers remove it;
    - `/feed`, and the filter in `_on_activity`;
    - the filter is cleared when the run ends;
    - `/feed` goes in `HELP`.
  - `README.md`: one or two sentences on the live row and `/feed`.
- Test: `tests/chat/test_controller_feed.py`, `tests/chat/test_controller_agents.py` (new)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `FEED_AGENTS`; `ChatController._feed_filter: str | None`; `ChatController._hidden: int`.

- [ ] **Step 1: Write the failing tests**

```python
def test_btw_shows_a_side_line_until_answered(controller):
    """/btw why did it take 3 tries? adds one SideJob whose label starts with '"why did it take 3 tries?'
    (quoted and cut to fit at 60 characters); a btw_answer event removes it; a btw_failed event removes it too."""


def test_feed_filter_shows_only_that_agent(controller):
    """/feed tester prints the 'Showing only the tester's lines…' line and sets state.view().feed_filter;
    an activity batch with an implementer end record and a tester end record prints only the tester's tool
    line; a milestone still prints; /feed then prints 'Showing everything again. 1 lines from other agents
    were hidden: /show <run> to see them.' and clears feed_filter."""


def test_feed_sub_agent_and_engine(controller):
    """/feed sub-agent shows only records with a sub_id; /feed engine shows only role engine; /feed implementer
    includes the implementer's sub-agent lines (role implementer, with sub_id)."""


def test_feed_unknown_and_nothing_hidden(controller):
    """/feed bogus prints 'Pick one of: implementer, tester, reviewer, architect, sub-agent, engine.' and sets
    no filter; /feed with nothing hidden prints 'Showing everything again.'"""


def test_the_filter_resets_when_the_run_ends(controller):
    """With /feed tester on, run_done clears the filter (state.view().feed_filter is None), and the next
    activity batch prints every line."""


def test_sub_agent_end_to_end(controller_following_a_scripted_run):
    """A scripted implementer turn calls fire_chain and fire_tool to open a task call ('explore tests') with a
    nested read_file whose parent chain leads to it, and polls the watcher while the task call is open (use an
    open start without an end for the task call, or poll between start and end): the live rows' plain
    text contains '└ sub-agent' and 'explore tests', and the printed feed line for the nested read comes
    from a record carrying sub_id."""
```

The `1 lines` grammar in `test_feed_filter_shows_only_that_agent` comes from the spec's copy, `{n} lines`. Make the copy grammatical instead: `1 line` and `n lines`. Update the test strings to match. That's a ruled correction of the spec's copy.

**Ruling:** these are specified by docstring. Use the existing controller harness, and make every assertion stated.

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

**The `/btw` side line.**
- In `_btw`, make `job = SideJob(_quoted(question), time.time())`, where `_quoted` wraps the question in quotes and cuts it at 60 characters with `…`.
- Call `self.state.add_side(job)`, and pass `job` through to the handlers via `failed_data` and the result.
- `_on_btw_answer` and `_on_btw_failed` call `remove_side(job)`. Store the job keyed by a counter if passing the object through event data is awkward.

**The `/feed` command.**
- `/feed` with an argument: if the argument is in `FEED_AGENTS`, set `self._feed_filter`, reset `self._hidden = 0`, call `state.set_feed_filter(agent)` and print the confirmation. Otherwise print the pick-one line.
- `/feed` with no argument: clear the filter and print the count line, or the nothing-hidden line.

**Filtering in `_on_activity`,** before rendering, keeping only end records that pass the filter:
- `sub-agent`: `"sub_id" in record`;
- `engine`: `record["role"] == "engine"`;
- any other agent: `record["role"] == agent`.

Add the end records that don't pass to `self._hidden`. Start records pass through, since the renderer ignores them anyway. Milestones are never filtered.

**When the run ends:** clear the filter in the shared run-clearing helper.

**Help:** add `/feed <agent> (show one agent's lines; /feed to show all)` to `HELP`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat tests/cli tests/ui -q -n 0`.

- [ ] **Step 5: Update the README, run the full suite, then commit**

Commit message: `Live row shows /btw questions; /feed filters the feed by agent`, plus the trailer.
