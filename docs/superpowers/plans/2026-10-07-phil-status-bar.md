# Status Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The chat's bottom line shows these, in one dense line, dropping segments in an agreed order when the terminal is narrow:
- the repo and branch;
- the model in use;
- run progress as task dots;
- tokens;
- cost against the budget.

**Architecture:**
- **Task 1:** `render_toolbar` becomes a pure function that returns prompt_toolkit fragments from an extended `ToolbarView`.
- **Task 2:** the engine records a raised budget (`budget_raised`), and the watcher passes it to the chat.
- **Task 3:** the controller keeps the new view fields current (repo, branch, model, tokens, run cost, budget), and the terminal draws fragments.

**Tech Stack:** Python 3.14, prompt_toolkit formatted text, Rich `cell_len`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-phil-status-bar-design.md`

**Base:** branch `plan-status-bar`, stacked on `callouts` (PR #31). Execute it there, and rebase onto `main` once #31 merges.

## Global Constraints

- **Editing and committing:**
  - Edit files only with the Edit and Write tools. Never edit through python, perl, sed, heredocs or printf in Bash.
  - To commit, write the message to a file with Write, then run `git commit -F <file>`.
  - Every message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, whatever model you are.
- **Safety:**
  - Keep the words "keychain" and "credentials" out of Bash command lines.
  - Never work around a hook or guard. Report BLOCKED.
  - Never read or print `.env` files.
- **Tests:**
  - Never run `-m live` or `-m bench`.
  - Iterate with `uv run pytest <paths> -q -n 0`. Run the full `uv run pytest -q` once at the end of each task.
  - Tests must be portable (CI runs Ubuntu, macOS and Windows).
- **Text I/O:** `encoding="utf-8"`, and writes use `newline="\n"`.
- **No markup:** repo, branch, goal and model text is plain text.
- **Constants:**

| Name | Value |
|---|---|
| `SEP` | `" │ "` |
| `BUDGET_WARN` | `0.8` |
| `MAX_DOTS` | `12` |

- **Copy, verbatim:**
  - `Phil · type a goal, or /help`
  - `⏸ {run_id} needs you`
  - `chat {cost}`
  - `{n} tok`
  - `/btw ×{n}`
  - `{n} parked`

## Review Focus

1. **A terminal resized narrower while a run is going:** the line never wraps, and the pause notice survives at any width of 20 or more. Tested in Task 1.
2. **No budget configured** (`max_cost_usd` = 0): the cost shows alone, with no division by zero and no colour. Tested in Task 1.
3. **A model id with no provider prefix, or with several colons** (`ollama:qwen3:8b`): the short name is correct, and nothing raises. Tested in Task 1.
4. **A chat reopened after "Keep going":** the limit shown is the raised one, read from the log. Tested in Task 2.
5. **`git` failing** (not a repo, a detached HEAD, git missing): the branch is shown as `?` or the short SHA, and the chat starts anyway. Tested in Task 3.

---

### Task 1: The status line renderer

**Files:**
- Modify:
  - `src/phil/chat/state.py`: the new `ToolbarView` fields, plus `ChatState` setters.
  - `src/phil/ui/toolbar.py`: `render_toolbar` returns fragments; new helpers.
  - `src/phil/ui/theme.py`: the prompt_toolkit rules also cover the toolbar's `phil.*` styles.
  - `tests/ui/test_toolbar.py`: rewrite the toolbar tests for the new format, keeping the live-row tests.

**Interfaces:**
- Produces:
  - **`ToolbarView`, new fields:**
    - `repo: str = ""`
    - `branch: str = ""`
    - `model: tuple[str, str] | None = None`, as (tier label, short name)
    - `tokens: int | None = None`
    - `run_cost: tuple[float, str] | None = None`, as (usd, source)
    - `budget_usd: float = 0.0`
  - **`ChatState` setters:**
    - `set_place(repo: str, branch: str)`
    - `set_model(model: tuple[str, str] | None)`
    - `set_run_usage(tokens: int | None, run_cost: tuple[float, str] | None)`
    - `set_budget(budget_usd: float)`
  - **`phil.ui.toolbar` helpers:**
    - `short_model(model: str) -> str`
    - `format_tokens(n: int) -> str`
    - `task_dots(done: int, total: int, now: float, paused: bool) -> list[tuple[str, str]]`
    - `budget_style(cost: float, limit: float) -> str`
    - `render_toolbar(view, now, width=None) -> list[tuple[str, str]]`
    - `toolbar_text(fragments) -> str`
  - **Constants:** `SEP`, `BUDGET_WARN`, `MAX_DOTS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_toolbar.py — replace the render_toolbar tests; keep the render_live_row tests
from rich.cells import cell_len

from phil.chat.state import RunView, ToolbarView
from phil.ui.toolbar import (
    budget_style, format_tokens, render_toolbar, short_model, task_dots, toolbar_text,
)

RUN = RunView(run_id="r-4f2a", keyword="calc", node="implement", tasks_done=1, tasks_total=3, started=0.0)
FULL = ToolbarView(repo="calc", branch="phil/r-4f2a", run=RUN, model=("low", "gemini-3.8-flash"),
                   tokens=182_000, run_cost=(0.41, "reported"), budget_usd=1.0)


def text(view, now=64.0, width=None):
    return toolbar_text(render_toolbar(view, now, width))


def test_short_model():
    assert short_model("openrouter:google/gemini-3.8-flash") == "gemini-3.8-flash"
    assert short_model("ollama:qwen3:8b") == "qwen3:8b"
    assert short_model("model-x") == "model-x"
    assert short_model("anthropic:claude-sonnet-5") == "claude-sonnet-5"


def test_format_tokens():
    assert format_tokens(950) == "950 tok"
    assert format_tokens(182_000) == "182k tok"
    assert format_tokens(1_250_000) == "1.2M tok"


def test_task_dots_pulse_and_pause():
    working = toolbar_text(task_dots(1, 3, now=0.0, paused=False))
    assert working == "●◉○"
    assert toolbar_text(task_dots(1, 3, now=0.5, paused=False)) == "●○○"
    paused = task_dots(1, 3, now=0.5, paused=True)
    assert toolbar_text(paused) == "●◉○" and any("phil.warn" in style for style, t in paused if t == "◉")
    assert toolbar_text(task_dots(0, 1, now=0.0, paused=False)) == "◉"
    assert toolbar_text(task_dots(13, 20, now=0.0, paused=False)) == "●●●…◉ 14/20"
    assert toolbar_text(task_dots(3, 3, now=0.0, paused=False)) == "●●●"


def test_budget_style_thresholds():
    assert budget_style(0.79, 1.0) == "class:phil.cost"
    assert budget_style(0.80, 1.0) == "class:phil.warn"
    assert budget_style(1.00, 1.0) == "class:phil.error"
    assert budget_style(5.0, 0.0) == "class:phil.cost"


def test_run_in_progress_full_width():
    assert text(FULL) == ("calc @ phil/r-4f2a │ r-4f2a ●◉○ implement · 1m 04s │ low·gemini-3.8-flash │ "
                          "182k tok │ $0.41/$1.00")


def test_idle():
    view = ToolbarView(repo="calc", branch="main", cost=(0.06, "reported"))
    assert text(view) == "calc @ main │ Phil · type a goal, or /help │ chat $0.06"


def test_working_on_a_goal():
    view = ToolbarView(repo="calc", branch="main", step="architect", step_started=52.0,
                       model=("high", "claude-sonnet-5"), cost=(0.06, "reported"))
    out = text(view)
    assert out.startswith("calc @ main │ ") and "Architect drafting · 12s │ high·claude-sonnet-5 │ chat $0.06" in out


def test_paused_shows_the_notice_and_hides_stage_elapsed_and_model():
    view = ToolbarView(repo="calc", branch="phil/r-4f2a", run=RUN, paused=True, model=("low", "x"),
                       tokens=182_000, run_cost=(0.41, "reported"), budget_usd=1.0)
    assert text(view) == "calc @ phil/r-4f2a │ ⏸ r-4f2a needs you │ r-4f2a ●◉○ │ 182k tok │ $0.41/$1.00"


def test_btw_and_parked_are_appended():
    view = ToolbarView(repo="calc", branch="main", btw_pending=2, parked=3)
    assert text(view).endswith("│ /btw ×2 │ 3 parked")


def test_no_budget_shows_cost_alone():
    view = ToolbarView(repo="calc", branch="b", run=RUN, run_cost=(0.41, "estimated"), budget_usd=0.0)
    assert text(view).endswith("│ ~$0.41")


def test_drop_order():
    # first to go: repo → model → tokens → elapsed → cost → progress; the pause notice never drops
    assert "calc @" not in text(FULL, width=80)
    w60 = text(FULL, width=60)
    assert "gemini" not in w60 and "calc @" not in w60
    w40 = text(FULL, width=40)
    assert "tok" not in w40 and "1m 04s" not in w40
    assert "r-4f2a ●◉○" in text(FULL, width=30)


def test_never_wraps_and_pause_survives():
    paused = ToolbarView(repo="calc", branch="b", run=RUN, paused=True, run_cost=(0.41, "reported"), budget_usd=1.0)
    for width in (20, 30, 40, 60, 80, 120):
        assert cell_len(text(FULL, width=width)) <= width - 1
        assert "⏸ r-4f2a needs you" in text(paused, width=max(width, 24))
```

**Ruling:** in `test_drop_order`, the exact widths at which each segment drops depend on the separator width. If an assertion is off by a segment, keep the drop **order** the test encodes, and adjust the width in the test (not the order). Note it in the report.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/ui/test_toolbar.py -q -n 0`
Expected: FAIL (imports).

- [ ] **Step 3: Implement**

In `state.py`, add the six fields to `ToolbarView` (with the defaults listed under Interfaces) and these setters:

```python
    def set_place(self, repo: str, branch: str) -> None:
        self._update(repo=repo, branch=branch)

    def set_model(self, model: tuple[str, str] | None) -> None:
        self._update(model=model)

    def set_run_usage(self, tokens: int | None, run_cost: tuple[float, str] | None) -> None:
        self._update(tokens=tokens, run_cost=run_cost)

    def set_budget(self, budget_usd: float) -> None:
        self._update(budget_usd=budget_usd)
```

In `toolbar.py`, replace `render_toolbar` and the `SEPARATOR` logic, keeping `SPINNER`, `STEP_LABELS`, `NODE_LABELS`, `elapsed`, `render_live_row` and `_fit`:

```python
from dataclasses import dataclass

SEP = " │ "
BUDGET_WARN = 0.8
MAX_DOTS = 12
Fragments = list[tuple[str, str]]

# Keep priority, higher survives longer (spec §2 drop order).
P_EXTRA, P_REPO, P_MODEL, P_TOKENS, P_ELAPSED, P_COST, P_PROGRESS, P_PAUSE = range(8)


@dataclass
class _Segment:
    priority: int
    fragments: Fragments
    glue: str = SEP  # what joins it to the segment before


def short_model(model: str) -> str:
    rest = model.split(":", 1)[1] if ":" in model else model
    return rest.rsplit("/", 1)[-1]


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tok"
    if n >= 1000:
        return f"{n // 1000}k tok"
    return f"{n} tok"


def task_dots(done: int, total: int, now: float, paused: bool) -> Fragments:
    total, done = max(total, 0), max(min(done, total), 0)
    current = done < total
    if paused:
        cur = ("class:phil.warn", "◉")
    else:
        cur = ("class:phil.gate.pass", "◉") if int(now * 2) % 2 == 0 else ("class:phil.muted", "○")
    if total > MAX_DOTS:
        frags: Fragments = [("class:phil.gate.pass", "●" * min(done, 3)), ("class:phil.muted", "…")]
        if current:
            frags.append(cur)
        frags.append(("class:phil.muted", f" {done + (1 if current else 0)}/{total}"))
        return frags
    frags = [("class:phil.gate.pass", "●")] * done
    if current:
        frags.append(cur)
        frags += [("class:phil.muted", "○")] * (total - done - 1)
    return frags


def budget_style(cost: float, limit: float) -> str:
    if limit <= 0:
        return "class:phil.cost"
    ratio = cost / limit
    if ratio >= 1:
        return "class:phil.error"
    if ratio >= BUDGET_WARN:
        return "class:phil.warn"
    return "class:phil.cost"


def toolbar_text(fragments: Fragments) -> str:
    return "".join(text for _, text in fragments)


def _segments(view: ToolbarView, now: float) -> list[_Segment]:
    segs: list[_Segment] = []
    if view.repo:
        segs.append(_Segment(P_REPO, [("", view.repo), ("class:phil.muted", f" @ {view.branch or '?'}")]))
    run = view.run
    if view.paused and run:
        segs.append(_Segment(P_PAUSE, [("class:phil.warn", f"⏸ {run.run_id} needs you")]))
    if run:
        progress: Fragments = [("class:phil.id", run.run_id), ("", " "),
                               *task_dots(run.tasks_done, run.tasks_total, now, view.paused)]
        if not view.paused:
            progress.append(("", f" {run.node or 'starting'}"))
        segs.append(_Segment(P_PROGRESS, progress))
        if not view.paused:
            segs.append(_Segment(P_ELAPSED, [("", elapsed(now - run.started))], glue=" · "))
    elif view.step:
        frame = SPINNER[int((now - view.step_started) * 8) % len(SPINNER)]
        label = STEP_LABELS.get(view.step, view.step)
        step = f"{frame} {label} · {elapsed(now - view.step_started)}"
        if view.cancelling:
            step += " (cancelling…)"
        segs.append(_Segment(P_PROGRESS, [("", step)]))
    else:
        segs.append(_Segment(P_PROGRESS, [("class:phil.muted", "Phil · type a goal, or /help")]))
    if view.model and not view.paused:
        tier, name = view.model
        segs.append(_Segment(P_MODEL, [("class:phil.muted", f"{tier}·"), ("", name)]))
    if run and view.tokens is not None:
        segs.append(_Segment(P_TOKENS, [("class:phil.muted", format_tokens(view.tokens))]))
    if run and view.run_cost is not None:
        cost, source = view.run_cost
        shown = format_cost(cost, source)
        if view.budget_usd > 0:
            shown += f"/${view.budget_usd:.2f}"
        segs.append(_Segment(P_COST, [(budget_style(cost, view.budget_usd), shown)]))
    elif not run and view.cost is not None:
        segs.append(_Segment(P_COST, [("class:phil.cost", f"chat {format_cost(*view.cost)}")]))
    if view.btw_pending:
        segs.append(_Segment(P_EXTRA, [("class:phil.muted", f"/btw ×{view.btw_pending}")]))
    if view.parked:
        segs.append(_Segment(P_EXTRA, [("class:phil.muted", f"{view.parked} parked")]))
    return segs


def _join(segs: list[_Segment]) -> Fragments:
    out: Fragments = []
    for i, seg in enumerate(segs):
        if i:
            out.append(("class:phil.muted", seg.glue))
        out += seg.fragments
    return out


def render_toolbar(view: ToolbarView, now: float, width: int | None = None) -> Fragments:
    """The status line as prompt_toolkit fragments; with `width`, segments drop (spec §2 order)
    until it fits in fewer cells than that, and a line that still doesn't fit is cut with …."""
    segs = _segments(view, now)
    if width is not None:
        limit = width - 1
        while len(segs) > 1 and cell_len(toolbar_text(_join(segs))) > limit:
            droppable = [s for s in segs if s.priority != P_PAUSE]
            if not droppable:
                break
            victim = min(droppable, key=lambda s: s.priority)
            index = segs.index(victim)
            segs.remove(victim)
            if index == 0 and segs:
                segs[0].glue = SEP
    frags = _join(segs)
    if width is not None and cell_len(toolbar_text(frags)) > width - 1:
        frags = [("", _fit(toolbar_text(frags), width))]
    return frags
```

Two notes on this code:
- **Elapsed time is its own segment, with `glue=" · "`, so it drops independently.** When the progress segment drops, an orphaned elapsed segment must drop with it. Make the loop drop the elapsed segment whenever the progress segment is gone; a one-line check is enough.
- **Which segment drops first.** The `min` over priorities drops the lowest priority, and among equal priorities (`/btw` and parked) the first in the list. That's acceptable.

In `theme.py`, `prompt_toolkit_styles()` also includes `phil.muted`, `phil.warn`, `phil.error`, `phil.gate.pass`, `phil.id` and `phil.cost`, under their own names (keep `live`). They must all translate. If one uses a colour that isn't in `_PT_COLORS` (for example `magenta`), add that colour to the map. Add a test that the toolbar's six styles are in the rules.

Update any other import of `SEPARATOR` (`grep -rn SEPARATOR src tests`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ui -q -n 0`
Expected: PASS.

`src/phil/cli/main.py`'s `toolbar()` closure is annotated as returning `-> str`. Change the annotation to return fragments; `TerminalIO` passes them straight to prompt_toolkit, which accepts a list of `(style, text)` tuples. Run `uv run pytest tests/chat tests/cli -q -n 0` to catch callers that compare the toolbar to a string, and update them to compare `toolbar_text(...)`, keeping their intent.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Status line: one dense line with repo, model, task dots, tokens and budget`, plus the trailer.

---

### Task 2: `budget_raised`, from the engine to the chat

**Files:**
- Modify:
  - `src/phil/run/engine.py`: the `continue` branch around line 861.
  - `src/phil/chat/watcher.py`: post `budget_raised`, and seed it from the log.
  - `src/phil/chat/controller.py`: `_on_budget_raised`, plus the kind in the accepted run events.
- Test: `tests/run/test_engine_budget.py` (or the existing budget test file; find it with `grep -rln "budget_limit_cost" tests`), the watcher tests, `tests/chat/test_controller_status.py` (new).

**Interfaces:**
- Consumes: `ChatState.set_budget` (Task 1).
- Produces:
  - The run event `budget_raised {max_cost_usd: float, max_tokens: int}`, with absolute limits.
  - The watcher posts `ChatEvent("budget_raised", {...})`.

- [ ] **Step 1: Write the failing tests**

```python
def test_continue_records_the_raised_limits(...):
    """Using the existing budget-escalation engine test pattern: answering a budget pause with
    continue appends exactly one budget_raised event whose max_cost_usd == run cost at that moment +
    config.run.max_cost_usd and max_tokens == tokens + config.run.max_tokens (the same values the
    state's budget_limit_* get)."""


def test_watcher_posts_budget_raised_and_seeds_it_from_the_log(watcher_setup):
    """A budget_raised already in events.jsonl when the watcher starts is posted on its first poll;
    a later one is posted when it appears; the same event is never posted twice."""


def test_the_chat_uses_the_raised_budget(controller):
    """A budget_raised event with max_cost_usd=2.4 sets controller.state.view().budget_usd == 2.4."""
```

**Ruling:** these are specified by docstring. Write them with the repo's existing engine, watcher and controller harnesses, and make every assertion the docstring states.

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

**Engine:** in `escalate`'s `continue` branch, after computing the new limits, append the event inside a guard so recording never fails the run (use the engine's existing `_milestone`-style guard or a `try/except Exception` with a log):

```python
            if self.deps.events is not None:
                try:
                    self.deps.events.append("budget_raised", max_cost_usd=new_cost, max_tokens=new_tokens)
                except Exception:
                    logger.warning("couldn't record the raised budget", exc_info=True)
```

**Watcher:**
- In `__init__`, read `self.events.latest("budget_raised")` and keep it as `self._budget_pending`, guarded (None on any error).
- On the first poll, if it's set, post it.
- During polling, post any `budget_raised` that arrives in the new events read by `_poll_feed`.
- Never post the same event twice.

**Controller:**
- Add `"budget_raised"` to the accepted run events.
- `_on_budget_raised(data)` calls `self.state.set_budget(float(data.get("max_cost_usd") or 0))`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/run tests/chat -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Record a raised budget and show it in the chat`, plus the trailer.

---

### Task 3: The controller fills the status line

**Files:**
- Modify:
  - `src/phil/chat/controller.py`: place, model, run usage and budget.
  - `src/phil/git.py`, or wherever the existing git helpers live: `current_branch(root) -> str`.
- Test: `tests/chat/test_controller_status.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces:
  - `current_branch(root: Path) -> str`: the branch name, or the short SHA on a detached HEAD, or `"?"` on any error. It never raises.
  - `ChatController._refresh_place()`
  - `ChatController._model_for(role: str | None) -> tuple[str, str] | None`
  - `STEP_ROLES = {"intake": None, "architect": "architect", "revise": "architect", "critic": "critic", "designing": "architect"}`. Here `None` means intake, which resolves to `classifier` if a classifier model is configured, otherwise `orchestrator`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/chat/test_controller_status.py
def test_current_branch(tmp_path, git_repo):
    """In a repo on branch main: "main". With HEAD detached: a 7+ character hex SHA. In a plain
    non-repo directory: "?". current_branch never raises."""


def test_place_is_set_at_start_and_after_a_run(controller_with_repo):
    """At chat start, state.view().repo == the repo folder name and .branch == its branch. When a run starts
    following, .branch == the run's record.branch. After run_done, .branch is re-read from the repo."""


def test_model_follows_the_working_role(controller):
    """With config models high="anthropic:claude-sonnet-5", low="openrouter:google/gemini-3.8-flash":
    - a step "architect" sets model == ("high", "claude-sonnet-5");
    - a live_step with role "implementer" sets ("low", "gemini-3.8-flash");
    - clearing the step with no run sets None."""


def test_run_usage_and_budget_come_from_run_progress(controller):
    """A run_progress event with tokens=182000, cost_usd=0.41, cost_source="reported" sets tokens and
    run_cost; when a run starts, budget_usd == config.run.max_cost_usd; after the run ends, tokens and
    run_cost are None."""


def test_status_line_end_to_end(controller_following_a_scripted_run):
    """After a scripted run's progress has been followed: toolbar_text(render_toolbar(view, now, 120))
    contains the repo name, "●", the low model's short name, and "/$" (the budget)."""
```

**Ruling:** these are specified by docstring. Write them with the existing controller and git fixtures, and make every assertion stated.

- [ ] **Step 2: Run them to verify they fail**

- [ ] **Step 3: Implement**

`current_branch(root)`:
- run `git rev-parse --abbrev-ref HEAD` through the existing git helper;
- if the result is `HEAD`, return `git rev-parse --short HEAD`;
- on any exception, return `"?"`.

**Controller:**
- `_refresh_place()`: `self.state.set_place(self.info.root.name, branch)`. The branch is the followed run's `record.branch` when a run is being followed (`get_run(self.conn, self._run_id)`), else `current_branch(self.info.root)`. Wrap it so it never raises.
- **Calls:** call it at startup, where the controller first sets its state, when a run starts being followed (`_follow`), and in `_on_run_done` after the run is cleared.
- **`_model_for(role)`:**
  - `None` (intake) resolves to `"classifier"` if the config has a classifier model, else `"orchestrator"`;
  - return `(config.model_owner(r), short_model(config.model_for(r)))`;
  - on any exception, return None.
- **Steps:** where the controller sets a step (`self.state.set_step(step, now)`, around the `_step` helper), also call `self.state.set_model(self._model_for(STEP_ROLES.get(step)) if step else self._run_model())`.
  - `_run_model()` is the live step's role model while a run is followed, else None.
  - Unknown steps give None.
- **`_on_live_step`:** set the model from the live step's role. When the live step clears and no goal step is running, set it to None.
- **`_on_run_progress`:**
  - also call `self.state.set_run_usage(data.get("tokens"), (data.get("cost_usd", 0.0), data.get("cost_source", "reported")))`;
  - when the run starts being followed, call `self.state.set_budget(self.config.run.max_cost_usd)`.
- **`_on_run_done` and `_forget_run`:** `set_run_usage(None, None)`, `set_model(None)` and `_refresh_place()`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat tests/cli tests/ui -q -n 0`
Expected: PASS.

- [ ] **Step 5: README**

Add one or two sentences to the chat section: what the status line shows (repo @ branch, the model at work, task dots, tokens, and cost against the run's budget, turning yellow from 80% and red at 100%), and that it drops detail on narrow terminals.

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Status line: the chat keeps repo, branch, model, usage and budget current`, plus the trailer.
