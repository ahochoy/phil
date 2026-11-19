# Full-screen chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `phil` opens the chat as a full-window Textual frame (header, scrolling conversation, live
line, boxed input), with inline and line IO kept as they are.

**Architecture:** A new `phil.ui.screen` package (the only importer of Textual) provides `ScreenIO`,
a drop-in for `TerminalIO`: the Textual app owns the main thread, and the chat driver (one or more
`ChatController`s in turn) runs on a worker thread. The controller keeps printing to a Rich
`Console`; in the frame that console is a `PaneConsole` whose prints become conversation items.
Feed lines reach the frame as structured task output through one optional, additive `ChatIO` field,
so the frame can group and fold a task's lines; inline and line output don't change.

**Tech Stack:** Python 3.14, Textual (new), Rich 15, Typer, prompt_toolkit (inline only), pytest
with Textual's pilot (run from sync tests through `asyncio.run`, no new test plugin).

**Spec:** `docs/superpowers/specs/2026-11-18-phil-full-screen-design.md`

## Global Constraints

- Only `src/phil/ui/screen/` imports `textual`; `cli/main.py` imports `phil.ui.screen` lazily (as it
  does `phil.chat.terminal`).
- Dependency: `textual>=8.2.8` (current release; keep-dependencies-current). No other new runtime or
  dev dependencies.
- `ChatIO`'s existing fields and meanings are unchanged: `ask` returns str | None (EOF) | `WAKE` and
  may raise `KeyboardInterrupt`; `choose` returns an answer | `TYPE` | `WAKE` | None. New fields are
  optional with defaults that keep today's behaviour.
- Inline (`[chat] screen = "inline"`, the small-terminal fallback, a legacy Windows console) and
  non-TTY (`LineIO`) output stay byte-for-byte as today: no fold, today's start (open-chats list and
  "Reopen one?"). Today's terminal and controller tests keep passing unchanged.
- Setting: `[chat] screen = "full" | "inline"`, default `"full"`.
- Fallback: a terminal smaller than **60 columns or 15 rows** at start uses inline and prints one
  line saying so.
- Copy, verbatim from the spec:
  - quiet line: `2 open chats · /chat` (`1 open chat · /chat` for one); none when there are none.
  - exit line: `To continue this chat:  phil --resume <id>` (two spaces after the colon).
  - folded task: `✓ Task 1 · Theme setting · done · 2 files · 0:38`, i.e. today's `task_done` band
    with ` · <title>` inserted after the task id.
- `/resume`, `phil resume <run>`, `phil --resume <id>` (id still required) and `--new` keep their
  meanings. `--new`'s help becomes `Start a new chat (the default).`
- Textual's clashing defaults are off: no command palette (`ENABLE_COMMAND_PALETTE = False`), no
  Ctrl+Q quit binding.
- Repo rules: edit files with Edit/Write only; tests run with
  `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`; never `-m live` or
  `-m bench`; commit via `.git/phil-commit-msg.txt` and `git commit -F`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A wake while the user is mid-word:** the typed text and cursor position survive in the input;
   nothing is sent. (Pinned in Task 7.)
2. **Output after the frame has closed** (the controller's `_closing` line, a late print during
   shutdown): dropped silently, never an exception or a traceback over the restored terminal.
   (Pinned in Task 6.)
3. **Ctrl+C with no prompt active, then the chat ends** (EOF or `/quit` before another `ask`): the
   held interrupt is discarded at exit; no stray `KeyboardInterrupt`, the exit line still prints.
   (Pinned in Task 7.)
4. **A multi-line paste into the input:** the newlines stay in the box; nothing is sent until Enter.
   (Pinned in Task 7.)
5. **Narrow pane / resize while a callout or a long line is showing:** nothing raises at 60 columns;
   items re-render at the new width. (Pinned in Task 9.)

---

## File map

| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | add `textual>=8.2.8` |
| `src/phil/config.py` | `ChatConfig.screen` |
| `src/phil/ui/fragments.py` (new) | prompt_toolkit-style fragments → Rich `Text` (no Textual) |
| `src/phil/ui/toolbar.py` | `FrameLive`, `render_frame_live` (the live line's content) |
| `src/phil/ui/feed_view.py` | `FeedRenderer.milestone(..., titled=True)` |
| `src/phil/ui/chats_view.py` (new) | `open_chat_line` (moved from `cli/main.py`), `open_chats_hint` |
| `src/phil/chat/decision.py` | `chat_picker_decision` |
| `src/phil/chat/controller.py` | `TaskOutput`, `ChatOutcome`, `ChatIO.task_output`/`copy`, `/chat`, `/status`, `/copy` |
| `src/phil/ui/screen/__init__.py` (new) | exports `ScreenIO`, `choose_mode` |
| `src/phil/ui/screen/mode.py` (new) | which IO to use (full / inline / line), and the fallback note |
| `src/phil/ui/screen/themed.py` (new) | `Themed`: renders a renderable under `PHIL_THEME` |
| `src/phil/ui/screen/console.py` (new) | `PaneConsole`: a Rich `Console` whose prints become items |
| `src/phil/ui/screen/widgets.py` (new) | the frame's widgets (tree below) |
| `src/phil/ui/screen/app.py` (new) | `PhilApp`: composition, CSS, bindings, thread-safe entry points |
| `src/phil/ui/screen/io.py` (new) | `ScreenIO`: the `ChatIO` adapter and `TaskOutput` |
| `src/phil/cli/main.py` | mode choice, the chat driver loop, `phil chat`, `--continue`, exit line |
| `docs/guide.md`, `docs/superpowers/roadmap.md`, `CHANGELOG.md` | documentation |
| `tests/ui/screen/` (new) | pilot tests; `tests/ui`, `tests/chat`, `tests/cli` additions |

Widget tree (names are the classes in `widgets.py`; CSS ids in brackets):

```
PhilApp
└─ Screen (default)
   ├─ HeaderBar   [#header]        ◆ phil · <repo> · <branch>               chat <id>
   ├─ Conversation [#conversation] VerticalScroll; children are Item / TaskBlock
   │  ├─ Item                       one printed renderable (Static over Themed(renderable))
   │  └─ TaskBlock                  header band + FeedLine Items + SubAgentLine Items; folds
   ├─ NewMarker   [#new]           "↓ new", shown only while scrolled up with unseen output
   ├─ LiveLine    [#live]          left rows / right status from render_frame_live; hidden if empty
   ├─ DecisionMenu [#menu]         a docked callout; hidden unless choose() is active
   └─ PromptBox   [#prompt]        TextArea subclass, bordered, height auto (max 6 lines)
```

Test helper used by every pilot test (create in Task 6, `tests/ui/screen/conftest.py`):

```python
def pilot_run(app, script, size=(100, 30)):
    """Run `script(pilot)` (async) against `app` under Textual's test driver, from a sync test."""
    async def main():
        async with app.run_test(size=size) as pilot:
            await script(pilot)
    asyncio.run(main())

async def until(pilot, predicate, timeout=5.0):
    """Pause the pilot until `predicate()` is true; fail the test after `timeout` seconds."""
```

---

### Task 1: Textual dependency and the `[chat] screen` setting

**Files:**
- Modify: `pyproject.toml` (dependencies), `uv.lock` (via `uv lock`)
- Modify: `src/phil/config.py:84-97` (`ChatConfig`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `ChatConfig.screen: Literal["full", "inline"] = "full"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_chat_screen_defaults_to_full():
    assert PhilConfig().chat.screen == "full"

def test_chat_screen_accepts_inline(tmp_path):
    config = load_config_from_text(tmp_path, '[chat]\nscreen = "inline"\n')  # use the file's existing loader helper
    assert config.chat.screen == "inline"

def test_chat_screen_rejects_other_values(tmp_path):
    with pytest.raises(ConfigError):  # the error type the file's other bad-value tests expect
        load_config_from_text(tmp_path, '[chat]\nscreen = "half"\n')
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/test_config.py -k screen`
Expected: FAIL (`ChatConfig` has no `screen`).

- [ ] **Step 3: Add `screen: Literal["full", "inline"] = "full"` to `ChatConfig`** with a comment
  pointing at the spec, and `"textual>=8.2.8"` to `dependencies`; run `uv lock` and
  `uv sync`. Confirm `phil config` shows/validates the key the way it does `intake` (no extra code
  if `phil config` walks the model).

- [ ] **Step 4: Run them to verify they pass**, then the whole suite (the lock changed).

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit** — `Config: [chat] screen, and the Textual dependency`

---

### Task 2: The live line's content (`render_frame_live`) and fragments to Rich

**Files:**
- Create: `src/phil/ui/fragments.py`
- Modify: `src/phil/ui/toolbar.py` (add after `render_live_rows`)
- Test: `tests/ui/test_toolbar.py`, `tests/ui/test_fragments.py` (new)

**Interfaces:**
- Consumes: `ToolbarView`, `render_live_rows`, `_segments`' cost logic (`format_cost`,
  `budget_style`, `elapsed`).
- Produces:
  - `to_text(fragments: Fragments) -> rich.text.Text` in `ui/fragments.py`: a `"class:phil.x"` style
    becomes Rich style `"phil.x"`; `""` no style; plain attribute words (`bold`) pass through; a
    `"\n"` fragment stays a newline.
  - `@dataclass(frozen=True) class FrameLive: left: list[Fragments]; right: Fragments` and
    `render_frame_live(view: ToolbarView, now: float, width: int, max_lines: int | None = None) -> FrameLive`.

Rules for `render_frame_live` (the spec's §5.2 live line):
- `left` is `render_live_rows(view, now, width - cells(right) - 2, max_lines)` split into one
  `Fragments` per line, with ` · task N/M` appended to the first row while a run has tasks
  (`N = tasks_done + 1`, capped at `tasks_total`).
- `right` while a run is followed: `elapsed(now - run.started)` and, when known, the run cost as the
  toolbar shows it (`format_cost`, ` / $budget` when a budget is set, `budget_style`), joined by
  ` · `. With no run but a goal step or `/btw` row: the chat cost (`format_cost(*view.cost)`) when
  known. Otherwise empty.
- Paused run: the first left row is `⏸ <run name or id> needs you` in `class:phil.warn` (the
  toolbar's pause segment), right side as for a run.
- Nothing running (no rows, not paused): `FrameLive([], [])` — the widget hides.

- [ ] **Step 1: Write the failing tests**

```python
def test_to_text_maps_classes_to_theme_styles():
    text = to_text([("class:phil.warn", "a"), ("", "b"), ("bold", "c")])
    assert text.plain == "abc"
    assert [str(s.style) for s in text.spans] == ["phil.warn", "bold"]

def test_frame_live_idle_is_empty():
    assert render_frame_live(ToolbarView(), now=100.0, width=80) == FrameLive([], [])

def test_frame_live_run_has_task_count_left_and_time_cost_right():
    view = ToolbarView(run=RunView(run_id="r1", keyword="", node="implement", tasks_done=1,
                                   tasks_total=3, started=28.0, name="dark-mode", title="Toggle"),
                       live=LiveStep(role="implementer", summary="edit settings.tsx", started=99.0),
                       run_cost=(0.06, "reported"))
    live = render_frame_live(view, now=100.0, width=80)
    assert toolbar_text(live.left[0]).endswith("· task 2/3")
    assert toolbar_text(live.right) == "1m 12s · $0.06"

def test_frame_live_sub_agents_add_rows():
    # view as above plus subs=(SubAgent(1, "find theme", "grep x", 95.0), SubAgent(2, "check tests", None, 97.0))
    assert len(render_frame_live(view, now=100.0, width=80).left) == 3

def test_frame_live_paused_shows_needs_you():
    # paused=True, run named "dark-mode"
    assert toolbar_text(render_frame_live(view, 100.0, 80).left[0]).startswith("⏸ dark-mode needs you")

def test_frame_live_fits_width():
    # a long live summary at width=60
    live = render_frame_live(view, 100.0, 60)
    assert all(cell_len(toolbar_text(row)) + cell_len(toolbar_text(live.right)) + 2 <= 60 for row in live.left[:1])
```

(Construct `RunView`/`LiveStep`/`SubAgent` with the fields `chat/state.py` defines; the literal
values above are what the assertions rely on. Check the exact `format_cost` output for
`(0.06, "reported")` and use it.)

- [ ] **Step 2: Run them to verify they fail** — `pytest -q tests/ui/test_toolbar.py tests/ui/test_fragments.py`; Expected: ImportError / NameError.

- [ ] **Step 3: Implement `to_text` and `render_frame_live`** as specified; reuse the existing cost
  and elapsed helpers rather than copying them (factor a small `_run_status(view, now) -> Fragments`
  out of `_segments` if that's the cleanest way to share it, keeping `render_toolbar`'s output
  unchanged).

- [ ] **Step 4: Run** `tests/ui/test_toolbar.py tests/ui/test_fragments.py`; Expected: PASS, and
  today's toolbar tests unchanged.

- [ ] **Step 5: Commit** — `UI: the full-screen live line's content`

---

### Task 3: The titled task-done line

**Files:**
- Modify: `src/phil/ui/feed_view.py` (`FeedRenderer.milestone`)
- Test: `tests/ui/test_feed_view.py`

**Interfaces:**
- Produces: `FeedRenderer.milestone(event: dict, width: int, *, titled: bool = False) -> Text`.
  `task_started` remembers the task's title. With `titled=True`, a `task_done` band inserts
  ` · <title>` after the task id when a title is known; every other output is identical to today.

- [ ] **Step 1: Write the failing tests**

```python
def test_titled_task_done_inserts_the_title():
    feed = FeedRenderer()
    feed.milestone({"kind": "task_started", "task": "Task 1", "title": "Theme setting",
                    "ts": "2026-11-18T10:00:00Z"}, 80)
    done = feed.milestone({"kind": "task_done", "task": "Task 1", "files": 2,
                           "ts": "2026-11-18T10:00:38Z"}, 80, titled=True)
    assert done.plain == "✓ Task 1 · Theme setting · done · 2 files · 38s"

def test_untitled_task_done_is_unchanged():
    # same two events, titled=False
    assert done.plain == "✓ Task 1 done · 2 files · 38s"
```

(The spec's `0:38` is illustrative; the band keeps today's `elapsed` format.)

- [ ] **Step 2: Run to verify failure** — `pytest -q tests/ui/test_feed_view.py -k titled`; Expected: TypeError (unexpected keyword).
- [ ] **Step 3: Implement** — a `_titles: dict[str, str]` filled on `task_started`, popped on `task_done`.
- [ ] **Step 4: Run** `tests/ui/test_feed_view.py`; Expected: PASS.
- [ ] **Step 5: Commit** — `Feed: a task-done line with the task's title`

---

### Task 4: Structured task output from the controller (`TaskOutput`)

**Files:**
- Modify: `src/phil/chat/controller.py` (`ChatIO`; `_print_feed`, `_end_burst`, `_on_milestone`,
  `_on_live_agents`; new `TaskOutput`)
- Test: `tests/chat/test_controller_feed.py`

**Interfaces:**
- Consumes: `FeedRenderer.milestone(..., titled=True)` (Task 3).
- Produces:

```python
class TaskOutput(Protocol):
    """Where the full-screen frame takes the feed; None on ChatIO means print it (inline, line)."""
    def task_started(self, task: str, band: Text) -> None: ...
    def task_line(self, task: str, line: Text) -> None: ...
    def sub_agent(self, task: str, seq: int, description: str) -> None: ...
    def task_done(self, task: str, folded: Text) -> None: ...
```

  and on `ChatIO`: `task_output: TaskOutput | None = None`.

Routing rules (only when `self.io.task_output` is not None; otherwise exactly today's prints):
- `_on_milestone`: `task_started` → `task_started(task, band)` and remember it as the open task;
  `task_done` → `task_done(task, milestone(data, width, titled=True))`; any other milestone with a
  task → `task_line(task, band)`; a milestone without a task → printed.
- `_print_feed` (after today's `/feed` filter): an item whose records carry a task → `task_line`,
  except items whose records are all sub-agent records (`_shows(record, "sub-agent")`), which are
  dropped; items without a task are printed.
- `_on_live_agents`: each sub-agent `seq` not yet announced for the open task →
  `sub_agent(open_task, seq, description)`; none while no task is open.

- [ ] **Step 1: Write the failing tests** (use the file's existing controller fixture and event
  helpers; `RecordingOutput` is a test double recording calls as tuples)

```python
def test_feed_goes_to_task_output_when_given(chat_with_run):
    out = RecordingOutput()
    controller = chat_with_run(task_output=out)
    send_milestone(controller, {"kind": "task_started", "task": "Task 1", "title": "Theme setting", "ts": T0})
    send_activity(controller, [end_record(task="Task 1", role="implementer", summary="read a.py")])
    send_milestone(controller, {"kind": "task_done", "task": "Task 1", "files": 2, "ts": T38})
    kinds = [call[0] for call in out.calls]
    assert kinds == ["task_started", "task_line", "task_done"]
    assert out.calls[-1][2].plain.startswith("✓ Task 1 · Theme setting · done")
    assert console_text(controller) == ""          # nothing printed for the task

def test_sub_agent_lines_are_announced_once_and_their_tool_lines_dropped(chat_with_run):
    # task_started, live_agents with subs seq 1 and 2 (twice), a sub-agent activity record
    assert [c for c in out.calls if c[0] == "sub_agent"] == [
        ("sub_agent", "Task 1", 1, "find where the theme is applied"),
        ("sub_agent", "Task 1", 2, "check existing settings tests")]
    assert not any(c[0] == "task_line" and "sub" in c[2].plain for c in out.calls)

def test_without_task_output_the_feed_prints_as_today(chat_with_run):
    # the same events with task_output=None: console output equals today's (compare to the
    # existing expected lines in this file)
```

- [ ] **Step 2: Run to verify failure** — `pytest -q tests/chat/test_controller_feed.py -k "task_output or sub_agent_lines or without_task_output"`; Expected: FAIL (`ChatIO` has no `task_output`).
- [ ] **Step 3: Implement** the protocol, the field and the routing above. `_open_task` resets with
  the feed (where `self._feed = FeedRenderer()` is reassigned at line ~2026).
- [ ] **Step 4: Run** `tests/chat/`; Expected: all pass (today's feed tests unchanged).
- [ ] **Step 5: Commit** — `Chat: hand the feed to a task output when the frame gives one`

---

### Task 5: `/chat`, `/status`, `/copy` and the run outcome

**Files:**
- Create: `src/phil/ui/chats_view.py` (move `_open_chat_line` from `cli/main.py:188-215` here as
  `open_chat_line`; `cli/main.py` imports it)
- Modify: `src/phil/chat/decision.py` (`chat_picker_decision`), `src/phil/chat/controller.py`
- Test: `tests/chat/test_chat_picker.py` (new), `tests/ui/test_chats_view.py` (new),
  `tests/chat/test_controller.py` (help list)

**Interfaces:**
- Produces:
  - `open_chat_line(n: int, chat: ChatSummary, width: int, *, now: datetime | None = None) -> Text`
    (unchanged behaviour) and `open_chats_hint(count: int) -> str | None` →
    `"2 open chats · /chat"`, `"1 open chat · /chat"`, None for 0.
  - `chat_picker_decision(chats: Sequence[tuple[str, str]]) -> Decision`: `(label, chat_id)` pairs;
    kind `"question"`, title `"Reopen which chat?"`, one `Option(label, answer=chat_id)` per chat,
    then `Option("Stay in this chat", answer="stay")`; default 0.
  - `@dataclass(frozen=True) class ChatOutcome: switch_to: str | None = None` in `controller.py`;
    `ChatController.run() -> ChatOutcome`.
  - `ChatIO.copy: Callable[[str], bool] | None = None` (True when copied).
  - `ChatController(..., pick_chat: bool = False)`: start in the picker (for `phil chat`).

Behaviour:
- `/chat`: lists `list_open_chats` minus this chat and minus chats `open_elsewhere`. None → prints
  `No other open chats.` Otherwise stage `"pick_chat"`, whose `_build_decision` is the picker.
  Answer `"stay"`, an empty typed line, or Esc-then-empty → back to the previous stage, printing
  nothing. A chat id (picked, or its number/id typed after Esc) → the chat is saved and `run()`
  returns `ChatOutcome(switch_to=id)`. `_save` stores the previous stage, never `"pick_chat"`.
- `pick_chat=True` at start: as `/chat`; with no open chats, prints
  `No open chats; starting a new one.` and stays idle.
- `/status`: prints, one per line and muted, the repo and branch, the model (`view.model`), the
  followed run's progress and cost (or "no run"), the chat's cost, and the parked count — what the
  old toolbar showed.
- `/copy`: the last reply (set where `render_answer` and `turn.reply` are printed) via
  `self.io.copy`; prints `Copied Phil's last reply.`; `Nothing to copy yet.` with no reply;
  `Copy isn't available here; select the text instead.` when `io.copy` is None or returns False.
- `HELP_COMMANDS` gains `("/chat", "reopen another open chat")`, `("/status", "repo, model, run and cost")`,
  `("/copy", "copy Phil's last reply")`, placed before `/help`.

- [ ] **Step 1: Write the failing tests**

```python
def test_open_chats_hint():
    assert open_chats_hint(0) is None
    assert open_chats_hint(1) == "1 open chat · /chat"
    assert open_chats_hint(2) == "2 open chats · /chat"

def test_chat_command_picks_another_chat(make_controller, open_chat):  # open_chat: a saved chat with a plan to approve
    io = ScriptedIO(choices=[open_chat.id])           # the file's scripted ChatIO double
    outcome = make_controller(io, script=["/chat"]).run()
    assert outcome == ChatOutcome(switch_to=open_chat.id)

def test_chat_command_stay_returns_to_the_stage(make_controller, open_chat):
    io = ScriptedIO(choices=["stay"])
    controller = make_controller(io, script=["/chat", "/quit"])
    assert controller.run() == ChatOutcome()
    assert io.prompts[-1] == controller._prompt()     # back at the idle prompt

def test_chat_command_with_none_open(make_controller):
    assert "No other open chats." in run_output(make_controller(ScriptedIO(), script=["/chat", "/quit"]))

def test_pick_chat_start_with_none_open_says_so(make_controller):
    assert "No open chats; starting a new one." in run_output(make_controller(..., pick_chat=True))

def test_copy_uses_io_copy(make_controller):
    copied = []
    io = ScriptedIO(copy=lambda text: copied.append(text) or True)
    # a question answered first (script a reply through the existing fake factory), then /copy
    assert copied == [last_reply_text]

def test_copy_without_io_copy_says_so(make_controller): ...
def test_status_lists_model_run_and_cost(make_controller): ...
def test_help_lists_chat_status_copy(): ...
def test_resume_still_continues_a_stopped_run(...):  # the existing /resume test, unchanged, still passes
```

- [ ] **Step 2: Run to verify failure** — `pytest -q tests/chat/test_chat_picker.py tests/ui/test_chats_view.py`; Expected: ImportError.
- [ ] **Step 3: Implement** as specified. The `cli/main.py` callers of `_open_chat_line` switch to
  `open_chat_line`; `_choose_open_chat` is otherwise untouched.
- [ ] **Step 4: Run** `tests/chat tests/ui tests/cli`; Expected: all pass.
- [ ] **Step 5: Commit** — `Chat: /chat picks another open chat; /status and /copy`

---

### Task 6: The frame: app, widgets, conversation items

**Files:**
- Create: `src/phil/ui/screen/__init__.py`, `themed.py`, `console.py`, `widgets.py`, `app.py`
- Test: `tests/ui/screen/__init__.py`, `tests/ui/screen/conftest.py` (the helper above),
  `tests/ui/screen/test_frame.py`

**Interfaces:**
- Produces:
  - `Themed(renderable: RenderableType)`: a Rich renderable that renders `renderable` inside
    `console.use_theme(PHIL_THEME)`, so `phil.*` style names resolve on Textual's console.
  - `PaneConsole(post: Callable[[RenderableType], None], width: Callable[[], int])`, a
    `rich.console.Console` subclass with `PHIL_THEME`: `print(*objects, **kwargs)` turns its
    objects into one renderable (a `str` through `render_str` with markup on; `Text`/other
    renderables as they are; several objects joined by `sep`) and calls `post`; `width` reads
    `width()`; `input` raises `RuntimeError` (the frame reads through `ChatIO`).
  - `PhilApp(App)`: `ENABLE_COMMAND_PALETTE = False`; `BINDINGS` without Ctrl+Q;
    `ALLOW_SELECT` on. Thread-safe entry points (each a no-op once the app has exited, never
    raising): `post_item(renderable)`, `set_header(repo: str, branch: str, chat_id: str)`,
    `clear_conversation()`. Items posted before the app mounts are kept and shown on mount.
  - `widgets.py`: `HeaderBar`, `Conversation`, `Item`, `TaskBlock`, `NewMarker`, `LiveLine`,
    `DecisionMenu`, `PromptBox` per the tree above (LiveLine/DecisionMenu/TaskBlock behaviour comes
    in Tasks 8–9; here they exist and are hidden).

Conversation behaviour (spec §5.2): follows new output while at the bottom; when the user has
scrolled up, a new item shows `NewMarker` and the view stays; reaching the bottom (scrolling, or any
key in `PromptBox`) hides it and resumes following. PgUp/PgDn and Shift+↑/↓ scroll the conversation
while focus is in the input; the wheel scrolls it.

- [ ] **Step 1: Write the failing tests**

```python
def test_themed_resolves_phil_styles():
    console = Console(record=True, color_system="truecolor", width=20)
    console.print(Themed(Text("x", style="phil.warn")))
    assert "\x1b[33m" in console.export_text(styles=True)   # yellow

def test_pane_console_posts_markup_as_text():
    posted = []
    PaneConsole(posted.append, lambda: 50).print("[phil.muted]hi[/]")
    assert posted[0].plain == "hi" and posted[0].spans[0].style == "phil.muted"

def test_header_and_items_show(pilot_run):
    app = PhilApp()
    app.set_header("my-site", "main", "c-20261118-120000")
    app.post_item(Text("hello"))
    async def script(pilot):
        assert "◆ phil · my-site · main" in render(app.query_one(HeaderBar))
        assert "chat c-20261118-120000" in render(app.query_one(HeaderBar))
        assert "hello" in render(app.query_one(Conversation))
    pilot_run(app, script)

def test_scrolled_up_new_output_shows_marker_and_holds_view(pilot_run):
    # post 100 items, scroll to top, post one more: NewMarker visible, scroll_y unchanged;
    # press a key in the prompt: marker hidden, at the bottom

def test_post_after_exit_is_dropped():          # Review Focus 2
    app = PhilApp()
    async def leave(pilot):
        pilot.app.exit()
    pilot_run(app, leave)
    app.post_item(Text("late"))                  # must not raise
```

(`render(widget)` in conftest: the widget's rendered text, e.g. via `widget.render()` / its
`Static.renderable` plain text; pick the stable public way in Textual 8.2.)

- [ ] **Step 2: Run to verify failure** — `pytest -q tests/ui/screen`; Expected: ImportError.
- [ ] **Step 3: Implement** the modules. CSS in `app.py` (`DEFAULT_CSS` / `CSS`): header one line,
  conversation `1fr`, live line `height: auto`, prompt bordered `height: auto; max-height: 8`
  (6 lines of text). Colours from the theme's cyan brand / muted dim.
- [ ] **Step 4: Run** `tests/ui/screen`; Expected: PASS. Also check
  `grep -rn "import textual\|from textual" src/phil | grep -v src/phil/ui/screen` prints nothing.
- [ ] **Step 5: Commit** — `Screen: the full-screen frame and conversation items`

---

### Task 7: `ScreenIO`: ask, wake, prefill, submit, EOF and Ctrl+C

**Files:**
- Create: `src/phil/ui/screen/io.py`
- Modify: `src/phil/ui/screen/app.py`, `widgets.py` (`PromptBox`)
- Test: `tests/ui/screen/test_io.py`

**Interfaces:**
- Consumes: `PhilApp`, `PaneConsole` (Task 6); `ChatIO`, `WAKE` (controller).
- Produces:

```python
class ScreenIO:
    def __init__(self, view: Callable[[], ToolbarView | None] = lambda: None) -> None: ...
    app: PhilApp
    console: PaneConsole                 # the controller's console in the frame
    def chat_io(self, spawn: Spawn) -> ChatIO   # ask, spawn, wake, submit, choose, prefill, copy, task_output=self
    def run(self, fn: Callable[[], T]) -> T     # app on this thread, fn on a worker; see below
    def close(self) -> None
    def width(self) -> int                      # the conversation pane's content width (80 before mount)
    def set_view(self, view: Callable[[], ToolbarView | None]) -> None
    def interrupt(self) -> None                 # Ctrl+C with no selection (called by the app)
```

Behaviour:
- **Input is always live.** Enter in `PromptBox` (non-empty or empty) sends the box's text to a
  line queue and clears the box; Alt+Enter, Shift+Enter and Ctrl+Enter insert a newline; a paste
  inserts as text (newlines kept).
- `ask(prompt)`: raises `KeyboardInterrupt` if one is held; returns `WAKE` if a wake is pending;
  otherwise returns the next queued line, blocking until a line, a wake (`WAKE`), EOF (None) or an
  interrupt (raise). The prompt text shows as the box's placeholder/label. A wake never touches the
  box, so typed text and cursor stay (Review Focus 1).
- `prefill(text)`: sets the box's text, cursor at the end.
- `wake()`: any thread; wakes a blocked `ask`/`choose`, else remembered for the next one.
- `submit(job)`: as `TerminalIO.submit` (daemon thread, `MAX_JOBS = 3` slots, never blocks).
- **EOF:** Ctrl+D in an empty box.
- **Ctrl+C:** the app's priority binding copies the selection when there is one
  (`screen.get_selected_text()`, `copy_to_clipboard`); otherwise `interrupt()`: raised at once from
  a blocked `ask`/`choose`, else held for the next one. A held interrupt is discarded when `run`'s
  `fn` returns (Review Focus 3).
- `run(fn)`: starts the app (`app.run()`); on mount starts `fn` on a daemon worker thread named
  `phil-chat`; when `fn` returns or raises, the app exits; `run` then returns `fn`'s value or
  re-raises its exception **after** the terminal is restored.
- Tests run the same machinery under `run_test`: expose `ScreenIO.start(fn)` (start the worker; used
  by `run`'s on-mount hook) so a test can do `io.start(fn)` inside `pilot_run(io.app, ...)`.

- [ ] **Step 1: Write the failing tests** (each starts a worker `fn` calling `chat_io` methods and
  recording results; `until(pilot, ...)` waits)

```python
def test_ask_returns_the_typed_line(): ...            # type "hello", Enter -> "hello"
def test_typing_before_ask_is_queued(): ...           # Enter while fn is busy; next ask returns it
def test_wake_mid_word_keeps_text_and_cursor(): ...   # type "hel", wake -> WAKE; box text "hel", cursor 3; then "lo"+Enter -> "hello"
def test_wake_before_ask_is_remembered(): ...         # wake(), then ask -> WAKE
def test_prefill_puts_text_in_the_box(): ...
def test_ctrl_d_on_empty_box_is_eof(): ...            # -> None
def test_ctrl_c_interrupts_a_blocked_ask(): ...       # KeyboardInterrupt raised in fn
def test_ctrl_c_while_busy_raises_at_next_ask(): ...
def test_held_interrupt_is_dropped_at_exit(): ...     # Ctrl+C while busy, fn returns: no exception from run/start
def test_ctrl_c_with_selection_copies_not_interrupts(): ...
def test_paste_keeps_newlines_and_does_not_send(): ...   # Review Focus 4: pilot paste "a\nb"; queue empty; box "a\nb"
def test_alt_enter_adds_a_line(): ...
def test_submit_runs_at_most_three_jobs_at_once(): ...   # 5 blocking jobs: 3 running until released
def test_run_reraises_fn_exception_after_exit(): ...    # fn raises ValueError; app not running; ValueError propagates
```

- [ ] **Step 2: Run to verify failure** — `pytest -q tests/ui/screen/test_io.py`; Expected: ImportError.
- [ ] **Step 3: Implement.** One result channel (a `queue.Queue` of lines plus flags under a lock)
  shared by `ask` and `choose`; app-side changes go through `app.call_from_thread`.
- [ ] **Step 4: Run** `tests/ui/screen`; Expected: PASS. Then run `test_io.py` five times in a
  row (`for i in 1 2 3 4 5; do <test command> tests/ui/screen/test_io.py || break; done`);
  Expected: five passes (thread timing is not flaky).
- [ ] **Step 5: Commit** — `Screen: ScreenIO answers the chat's prompts`

---

### Task 8: `ScreenIO.choose`: the docked decision menu

**Files:**
- Modify: `src/phil/ui/screen/io.py`, `widgets.py` (`DecisionMenu`)
- Test: `tests/ui/screen/test_choose.py`

**Interfaces:**
- Consumes: `callout_lines(decision, highlighted, width, live=True)`, `to_rich` (ui/callout),
  the result channel (Task 7).
- Produces: `ScreenIO.choose(prompt: str, decision: Decision) -> object`, same contract as
  `TerminalIO.choose`: ↑/↓ wrap, Enter picks, 1–9 pick at once, Esc → `TYPE`, `WAKE` keeps the
  highlight for the next menu of the same decision, Ctrl+D → None, Ctrl+C as in Task 7. While the
  menu shows, focus is on it, the box's text is kept but takes no keys, and the live line collapses
  to one line (`max_lines=1`). When it closes the menu is removed (never left in the conversation).

- [ ] **Step 1: Write the failing tests** (port each `TerminalIO.choose` test in
  `tests/chat/test_terminal.py` to the pilot: same decisions, same expected returns)

```python
def test_choose_enter_picks_default(): ...
def test_choose_arrows_wrap(): ...
def test_choose_digit_picks_at_once(): ...
def test_choose_escape_returns_type(): ...
def test_choose_wake_keeps_highlight_for_same_decision(): ...
def test_choose_wake_resets_for_a_different_decision(): ...
def test_choose_keeps_box_text(): ...                 # "draft" in box before; menu; pick; box still "draft"
def test_choose_collapses_live_line_to_one_row(): ...
```

- [ ] **Step 2: Run to verify failure**; Expected: AttributeError (`choose`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/ui/screen`; Expected: PASS.
- [ ] **Step 5: Commit** — `Screen: decisions as a docked menu`

---

### Task 9: Task blocks that fold, and the live line in the frame

**Files:**
- Modify: `src/phil/ui/screen/io.py` (the `TaskOutput` methods), `widgets.py` (`TaskBlock`,
  `LiveLine`), `app.py` (a 0.5 s refresh timer for the live line)
- Test: `tests/ui/screen/test_tasks.py`, `tests/ui/screen/test_live.py`

**Interfaces:**
- Consumes: `TaskOutput` (Task 4), `render_frame_live`, `to_text` (Task 2), `ScreenIO.set_view`.
- Produces: `ScreenIO` implements `TaskOutput`:
  - `task_started(task, band)` mounts a `TaskBlock` (header = band) in the conversation;
  - `task_line(task, line)` appends a line to that task's block (a task with no block gets one,
    headed by the line's task id);
  - `sub_agent(task, seq, description)` appends `└ sub-agent  <description>` (in `phil.sub`) once;
  - `task_done(task, folded)` folds the block: its lines are removed and the header becomes
    `folded`. A later `task_line` for a folded task opens it again (a retry).
  - `LiveLine` shows `render_frame_live(view(), time.time(), pane width, max_lines)` : each left row
    as a line, the right side right-aligned on the first line; hidden when empty.

- [ ] **Step 1: Write the failing tests**

```python
def test_task_lines_group_under_their_block(): ...      # started, 2 lines: one TaskBlock, 2 lines inside
def test_task_done_folds_to_one_line(): ...
    # after task_done: block has no line children; its text == "✓ Task 1 · Theme setting · done · 2 files · 38s"
def test_sub_agent_line_shows_once(): ...
def test_two_tasks_two_blocks_in_order(): ...
def test_live_line_hidden_when_idle(): ...
def test_live_line_shows_agent_task_count_and_cost(): ...   # view from Task 2's run test: "task 2/3" left, "$0.06" right
def test_live_line_grows_with_sub_agents_and_shrinks_back(): ...
def test_resize_to_60_columns_rerenders_without_error(): ...  # Review Focus 5: a long Text item, a callout
    # showing via choose, then pilot.resize_terminal(60, 20); no exception, items still present
```

- [ ] **Step 2: Run to verify failure**; Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/ui/screen`; Expected: PASS.
- [ ] **Step 5: Commit** — `Screen: tasks fold when done; the live line`

---

### Task 10: Mode choice and the chat driver loop in the CLI

**Files:**
- Create: `src/phil/ui/screen/mode.py`
- Modify: `src/phil/cli/main.py` (`root` options, `_chat`, `_run_chat`, new `chat` command,
  `_terminal`/new `_screen` test seams)
- Test: `tests/ui/screen/test_mode.py`, `tests/cli/test_chat_command.py`,
  `tests/cli/test_banner_startup.py`

**Interfaces:**
- Consumes: `ScreenIO` (Tasks 7–9), `ChatOutcome`, `pick_chat`, `open_chats_hint`,
  `list_open_chats`, `ChatConfig.screen`.
- Produces:
  - `choose_mode(setting: str, tty: bool, columns: int, rows: int, windows_console_ok: bool) -> tuple[str, str | None]`:
    `("line", None)` without a TTY; `("inline", None)` for `"inline"`; `("inline", "This terminal is
    smaller than 60×15; using the inline chat.")` below 60 columns or 15 rows; `("inline", "This
    console can't show the full-screen chat; using the inline chat.")` when `windows_console_ok` is
    False; else `("full", None)`. `windows_console_ok` is True off Windows, and on Windows when
    `WT_SESSION` or `TERM_PROGRAM` is set (Windows Terminal, VS Code).
  - `_screen(view)` in `cli/main.py`: builds `ScreenIO(view)` (patched in tests, like `_terminal`).
  - `phil chat` (Typer command `chat`): the chat with `pick_chat=True`.
  - `--continue` (root option, bool): reopen `list_open_chats(...)` 's first chat not
    `open_elsewhere`; none → `No open chat to continue; starting a new one.` Combining it with
    `--resume` or `--new` errors like `--resume`/`--new` do today.
  - `--new` help: `Start a new chat (the default).`
  - `_exit_line(chat_id: str, saved_stage: str, open_ids: set[str]) -> str | None`:
    `To continue this chat:  phil --resume <id>` when the chat is in `open_ids` or its saved stage
    isn't `"idle"`; else None.

Driver (one function used by every mode, replacing `_run_chat`'s single controller):
`_drive(io_adapter, out, ..., session, resume, pick_chat) -> ChatSession` loops: lock the session
(`ChatLocked` → print today's error; in the frame after a switch, stay on the previous chat and say
so), `diagnostics.redirect(session.dir / LOG_FILE)`, construct the controller with
`io_adapter.chat_io(...)`, point the adapter's view at it, `outcome = controller.run()`, unlock; on
`outcome.switch_to` open that chat (`resume=True`, in the frame `clear_conversation()` and
`set_header(...)`) and loop; otherwise return the last session.

Start per mode:
- **full:** no "Reopen one?" list; the banner card (or `banner_plain`) posted as the first items,
  then `open_chats_hint(len(listed))` as a muted item when it's not None (a listing failure hides
  it); `screen.run(lambda: _drive(...))`; after it returns (frame closed), print `_exit_line(...)`
  when not None. Any exception: the terminal is restored by `ScreenIO.run`, then today's crash
  handling runs.
- **inline / line:** exactly today's start (banner, open-chats list, "Reopen one?"), then `_drive`
  with `TerminalIO`/`LineIO`. A `choose_mode` note prints before the banner.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("args, expected", [
    (("full", False, 120, 40, True), ("line", None)),
    (("inline", True, 120, 40, True), ("inline", None)),
    (("full", True, 59, 40, True), ("inline", "This terminal is smaller than 60×15; using the inline chat.")),
    (("full", True, 120, 14, True), ("inline", "This terminal is smaller than 60×15; using the inline chat.")),
    (("full", True, 120, 40, False), ("inline", "This console can't show the full-screen chat; using the inline chat.")),
    (("full", True, 60, 15, True), ("full", None)),
])
def test_choose_mode(args, expected):
    assert choose_mode(*args) == expected

def test_exit_line():
    assert _exit_line("c-1", "approval", set()) == "To continue this chat:  phil --resume c-1"
    assert _exit_line("c-1", "idle", {"c-1"}) == "To continue this chat:  phil --resume c-1"
    assert _exit_line("c-1", "idle", set()) is None
```

  CLI tests (CliRunner, `_is_tty` patched True, `_screen` patched to a `FakeScreen` that runs `fn`
  inline and records posted items and the header):
  - full start shows the banner item and `2 open chats · /chat`, and never prints `Reopen one?`;
  - full start with no open chats posts no hint;
  - after an empty chat exits, stdout has no `To continue this chat`; after a chat left at
    approval, stdout ends with `To continue this chat:  phil --resume <id>`;
  - `[chat] screen = "inline"` uses `_terminal` and today's start (the existing banner/startup
    tests pass unchanged);
  - a 50-column terminal prints the smaller-than note and uses `_terminal`;
  - `phil chat` with two open chats: the controller starts in the picker; choosing one switches,
    the driver opens it with `resume=True` and the header shows its id;
  - `phil chat` with none: `No open chats; starting a new one.`;
  - `--continue` reopens the most recent open chat; with none, says so and starts new;
    `--continue --new` errors;
  - switching to a chat that another process holds: the error line, and the driver stays on the
    current chat;
  - `fn` raising inside the frame: the exception reaches today's crash handler (crash file written).

- [ ] **Step 2: Run to verify failure** — `pytest -q tests/ui/screen/test_mode.py tests/cli/test_chat_command.py tests/cli/test_banner_startup.py`; Expected: FAIL.
- [ ] **Step 3: Implement** the mode choice, the driver, the start per mode, `phil chat`,
  `--continue`, `--new`'s help and the exit line. Terminal size comes from
  `shutil.get_terminal_size()`.
- [ ] **Step 4: Run the whole suite** — `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`; Expected: all pass.
- [ ] **Step 5: Commit** — `CLI: the chat opens full screen; phil chat and --continue`

---

### Task 11: Documentation

**Files:**
- Modify: `docs/guide.md` (the chat section: the frame, keys, `/chat`, `/status`, `/copy`,
  `phil chat`, `--continue`, `[chat] screen`, the fallbacks; `--new` as the default),
  `docs/superpowers/roadmap.md` (lines 94–97: the inline decision reversed on 2026-11-18, pointing
  at the spec; line 134: full screen moved before launch), `CHANGELOG.md` (Unreleased)
- Test: `tests/release/` doc checks if any cover command lists (run them)

- [ ] **Step 1:** Write the guide text from the spec's §3 and §5, in the guide's existing voice;
  list every new command and key once.
- [ ] **Step 2:** Run `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/release tests/test_contract_descriptions.py`; Expected: PASS.
- [ ] **Step 3: Commit** — `Guide: the full-screen chat`

---

### Task 12: Live check (user-run)

Not automated (spec §6 "Live"). After Task 11, hand the user:

```
uv tool install --editable . --force
cd <a test repo> && phil
```

and the checklist: the banner and open-chats line; a real goal through to a running task; the live
line with sub-agents; a task folding; scroll up and see `↓ new`; resize; drag-select and Ctrl+C
copy, and `/copy`; `/chat`; quit and run the printed `phil --resume <id>`. Record what they report;
fixes go in follow-up commits on this branch.
