# UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each run gets a short readable name. Each fact shows in one place: the status bar tracks the task, and the live rows and the feed show which agent is acting. Tool calls collapse into a rolling view while they run and one summary line when their burst ends. Clarifying questions, the start-up lines and the parked count stop repeating.

**Architecture:**
- **Names:**
  - `phil.run.naming.run_name(goal, run_id)` builds a slug from `Goal.name`, which intake now fills.
  - `prepare_run` stores it in a new `runs.name` column.
  - `phil.ui.runs.run_label` / `label_for` turn it into `name (id)` or `name`.
  - `ChatState` remembers which runs a chat has already named in full.
- **Status bar and live rows:**
  - The watcher's `run_progress` event now carries the run's name and the current task's title.
  - `ui/toolbar.py` renders the new segments and drop order.
  - The main live row loses its task.
- **Bursts:**
  - A pure model in `ui/bursts.py` covers folding, the summary text and the rolling lines.
  - `FeedRenderer` holds the open burst and an index of closed ones.
  - The chat controller and `phil attach` print what it returns.
  - `ToolbarView.rolling` carries the rolling lines to the live area.
- **Repeats:**
  - **Clarifying questions:** `_ask_next` stops printing a question its callout asks. `_answers` prints a record line.
  - **Start-up:** the banner leaves out the run and other-chats lines when the open-chats picker lists them, and drops the tips line and the parked count.

**Tech Stack:** Python 3.14, pydantic, rich, prompt_toolkit fragments, sqlite3, pytest (xdist).

**Spec:** `docs/superpowers/specs/2026-10-16-phil-ui-polish-design.md`

**Base:** branch `ui-polish` (the spec commits).

## Global Constraints

- **Editing and committing:**
  - Edit files only with the Edit and Write tools. Never edit through python, perl, sed, heredocs (including empty ones) or printf in Bash.
  - To commit, write the message to `.git/phil-commit-msg.txt` with Write, then run `git commit -F .git/phil-commit-msg.txt`.
  - Every commit message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`, whatever model you are.
  - Stage only your own files (name them in `git add`; never `git add -A` or `.`).
- **Tests:**
  - Never run `-m live` or `-m bench`.
  - Iterate with `uv run pytest <paths> -q -n 0`.
  - Run the full `uv run pytest -q` once at the end of each task, before committing. It takes about 6 minutes.
  - Tests set `PHIL_HOME` to a temp dir (the suite's conftest already does).
  - New tests must be portable (CI runs Ubuntu, macOS and Windows) and keep the output clean.
- **Styles:** the `prompt_toolkit_style` converter (`src/phil/ui/theme.py`) accepts only plain ANSI colours and attributes. Any new toolbar or live-row style must be added to `_TOOLBAR_STYLES`. This plan adds none: the rolling lines use `class:phil.muted`.
- **Rendering:** model- or user-supplied text (names, titles, goals, answers) is never Rich markup. Build it with `Text`, or `escape()` it inside markup.
- **Names never fail anything.** A missing or bad intake name is replaced, never rejected. A run with no name is shown by its ID.
- **Command hints always print the ID:** `phil resume r-4f1c`, `phil diff r-4f1c`, `phil attach r-4f1c`, `phil pr r-4f1c`.
- **Not in this pass:** hiding internal-step lines ("Not sure how big this is · intake decides"), and the mascot.
- **Values, verbatim from the spec:**

| Name | Value |
|---|---|
| Name pattern | `^[a-z0-9]+(-[a-z0-9]+){1,2}$`, at most 32 characters |
| Stop words | `a an the to of for and or in on with add make create update fix change new some our my` |
| Last-resort name | `run-<id>` (for `r-4f1c`: `run-4f1c`) |
| `Goal.name` description | `2 to 3 lowercase keywords naming the goal, joined by hyphens, e.g. `newsletter-cta`. Leave null while `open_questions` is non-empty.` |
| Labels | first mention `newsletter-cta (r-4f1c)`, later `newsletter-cta`, unnamed `r-4f1c` |
| Status bar during a run | `roi-calculator @ main │ newsletter-cta · task 2 of 4 · Add signup form │ 3m 12s │ $0.42 / $2.00` |
| Planning / paused | `newsletter-cta · planning` / `repo @ branch │ ⏸ newsletter-cta needs you` |
| Drop order (first drops first) | `/btw` and parked → repo @ branch → elapsed → cost → run name and task; the pause notice never drops |
| Title floor | 12 cells, cut with `…`, before any segment drops |
| Live area cap | `MAX_LIVE_LINES = 6` lines in all |
| Rolling view | ≤ 3 calls: all; more: first two, `… N more`, the latest |
| Summary line | `    implementer ran 9 tools · edited src/index.html, build.mjs  (/more #14)` |
| Summary second part | `edited` + up to 3 files then `+N`; else `ran` + the last shell command cut to 40 characters; else nothing |
| Record line | `? How should visitors sign up …? → Button linking to an external URL` |
| Open-chats row | `1. newsletter-cta · run failed · Add a CTA section below the lead capture form…` |

## Rulings made while planning

These cover points where the spec is silent, or where the code makes a detail awkward. Executors follow them.

- **Ruling R1 (module paths):** `run_name` lives in `src/phil/run/naming.py` (`phil.run.naming`), not in a new `phil.runs` package. The repo's run package is `phil.run`, and a `phil.runs` beside it would invite wrong imports. `run_label` goes in a new `src/phil/ui/runs.py` as the spec says, with `label_for(run_id, name, *, first)` beside it for callers that hold only an ID and a name.
- **Ruling R2 (words):** a word is a whitespace-separated token with every character outside `a-z0-9` removed (`sign-up` → `signup`, `README.md` → `readmemd`). Tokens left empty are dropped. This is the most literal reading of "keeping only a-z and 0-9".
- **Ruling R3 (long names):** an intake slug over 32 characters is replaced by the built name, not cut, because a bad name is only ever replaced. If cutting the built name at hyphens leaves fewer than two words, the name is `run-<id>`, because the pattern needs two words.
- **Ruling R4 (`prepare_run` and `phil run`):**
  - `prepare_run` takes `goal: Goal | None`, not a name, because the fallback needs the run ID that `prepare_run` allocates.
  - `phil run` reads a plan file and has no goal, so its runs stay unnamed. The spec's "when it has a goal" never holds today.
- **Ruling R5 (redrawn text uses the bare name):** the status bar, callout titles (pause, PR) and the folded-decision prompt show the bare name, or the ID when there's no name, and never mark a mention.
  - **Why:** they are redrawn constantly, and the fold and details logic compares decisions for equality. A title that changed from `name (id)` to `name` between redraws would break that.
  - The spec's status-bar example shows the bare name too.
- **Ruling R6:** `ChatState.labelled_runs` isn't saved. A reopened chat names its run in full again on its first mention, because the earlier mention is in a terminal that's gone.
- **Ruling R7 (the status bar's other states):**
  - **Every task done:** the run segment shows the stage label, e.g. `newsletter-cta · Reviewing`, from `NODE_LABELS`.
  - **Paused:** the bar keeps the `/btw` and parked extras. Elapsed time and cost are left out, as the spec's example shows.
  - **Cost:** reads `$0.42 / $2.00`, with spaces as in the spec (today it's `$0.41/$1.00`).
- **Ruling R8 (the task title):** the watcher reads the run's saved plan once. It sends task `tasks_done + 1`'s description, with whitespace collapsed, as `title` in `run_progress`, along with `name`.
  - This works for reopened chats too, which never see `task_started` milestones written before their watcher started.
- **Ruling R9 (removed bar segments):**
  - `task_dots`, `MAX_DOTS` and `format_tokens` are deleted, with their tests.
  - `ToolbarView.tokens` stays, because `set_run_usage` still records it, but nothing renders it.
  - The model segment still shows when no run is followed.
- **Ruling R10 (the main live row):**
  - With an open call, the main row is `⠋ role · summary · elapsed` (§4.2).
  - Between calls it stays today's stage label (`Implementing`), with the rolling lines under it.
  - **Why:** §5.2's `⠋ implementer · 42s` would need a burst start time that the live step doesn't carry.
- **Ruling R11 (where a burst ends):** activity records carry no invocation ID, so a burst is keyed by `(task, role)`.
  - **It ends on:**
    - any record, start or end, with another key;
    - a milestone;
    - an `escalation` (a pause);
    - the run's end.
  - Two invocations of one agent on one task with nothing between them merge. In practice the engine always writes a gate record or a milestone between them.
- **Ruling R12 (engine records):** records from the engine's own gate runs (role `engine`) are not a burst. They keep today's one tool line each and end any open agent burst.
  - So a red gate that fails as expected is not printed as a failure line.
- **Ruling R13 (sub-agent calls):** a sub-agent's inner calls (records with `sub_id`) count in their parent agent's burst and are listed with it, because it is one invocation with one role.
  - The rolling view shows only the main agent's own calls, including its `task` call once that ends. Sub-agents keep their `└` row.
- **Ruling R14:** in the rolling view, `… N more` counts display items, so a folded look group counts as one. This follows "counts as one call for display".
- **Ruling R15 (`/more #n` for a burst):**
  - **Its number:** a burst is numbered by its last call's seq.
  - **What prints:** `/more #n` looks in the chat's burst index first and prints the burst's calls as today's tool lines, with no 20-line cap. If step `n` also has a detail, its panel follows, so nothing becomes unreachable.
  - **How long they last:** closed bursts are kept until the chat follows another run (`_forget_run`). A reopened chat has none, and `/more #n` falls back to the step's detail as today.
- **Ruling R16 (the live area's cap):** six lines in all, the `+N more` line included. With a callout up (`max_lines=1`), the ` +N more` suffix counts other agents' rows only, not rolling lines.
- **Ruling R17 (the failure line):** the failure line keeps today's tool-line layout:
  - its body in the error style (`phil.error`);
  - its result in `phil.gate.fail`;
  - its `#seq` reference kept.
- **Ruling R18 (`phil attach`):** attach prints the summary without `(/more #n)`, because attach has no `/more`. Failure lines keep their `#seq`, which `phil show <run> --step n` reads.
- **Ruling R19 (`/feed`):**
  - The hidden count stays "end records from other agents".
  - A summary shows when any of its records passes the filter. So `/feed sub-agent` shows the summaries of bursts that used a sub-agent.
- **Ruling R20 (free-text questions):** `_ask_next` prints nothing for a question that has options when a menu exists. A free-text question (no options) has no callout, so it keeps its printed line.
  - `_build_decision` now returns `None` for a free-text question.
  - **Why:** today it passes `None` to `_with_details`, which raises on every loop, so the chat spins. This was found while planning; a probe hung.
- **Ruling R21 (when the record line prints):**
  - Only for a question answered through its callout, picked or typed.
  - It is printed by `_answers` and replaces the `Answer: …` settled line for clarifying questions.
  - "Plan with what you know" and the approach picker keep their settled lines.
- **Ruling R22 (the record line's text):** muted style, control characters shown as `?` (`printable`), cut to the console width with `…` (`phil.ui.toolbar._fit`).
- **Ruling R23 (the open-chats row):**
  - **No chat ID:** the row leaves it out, as the spec says.
  - **`open elsewhere`:** goes between the state and the goal, so a cut never hides it.
  - **The run:** shown by its bare name, as in the spec's example; the user picks by number.
  - **No run:** a chat with no run (a plan waiting for approval) shows its goal, or `(no goal)` when it has none.
- **Ruling R24 (the banner's run line):** the banner's run line uses the first-mention label `name (id)`, and its command hint keeps the ID.
- **Ruling R25 (the budget warning):** the chat passes its label to `budget_warning_line`; `phil attach` keeps passing the ID.
- **Ruling R26 (`BannerFacts`):** `BannerFacts` loses `parked` and gains `run_label: str = ""`. An empty label means the ID.

## Review Focus

1. **An objective with no ASCII keywords** (`修复 登录`, or all stop words). The name falls back to `run-<id>`, still matches the pattern, and nothing raises. Test in Task 1.
2. **Runs created before this change** (`name` is NULL). They show by ID in `phil runs`, `phil show`, the chat, the banner and the open-chats list, and never as `None` or `None (r-…)`. Tests in Tasks 3 and 8.
3. **A run that fails or stops in the middle of a burst.** The burst's summary prints exactly once, before the run-end notice, in the chat and in `phil attach`. Tests in Task 6.
4. **A narrow terminal with a wide-character task title** (`日本語…`) and a long run name. No status-bar or live-area line is ever wider than `width - 1` cells, and the pause notice survives. Test in Task 4.
5. **A free-text clarifying question when a menu exists.** The question prints once, the chat doesn't spin, and no record line or callout appears. Test in Task 7.

## Task order

The user's suggested shape was kept, with one split: the burst work is Task 5 (the pure model, the renderer and the live area, all in `ui/`) and Task 6 (wiring it into the chat, `/more`, `/feed` and `phil attach`). A reviewer could reject the wiring, for example the `/more` collision ruling, while approving the model. Each half has its own test cycle, and Task 5 leaves every existing caller working because `tool_lines` is kept.

| # | Task |
|---|---|
| 1 | Run names: `run_name`, `Goal.name`, the intake prompt line |
| 2 | `runs.name` column, `create_run`/`RunRecord`, `prepare_run(goal=)`, the chat passes its goal |
| 3 | Labels: `run_label`, `ChatState.labelled_runs`, every chat mention, `phil show`/`/show`/`phil runs` |
| 4 | Status bar segments and drop order; the live rows name the agent only |
| 5 | Burst model and renderer; rolling lines in the live area (cap 6) |
| 6 | Bursts wired into the chat, `/more #n`, `/feed`, `phil attach` |
| 7 | Callouts: no plain-text copy of a question; the record line |
| 8 | Start-up: the merged open-chats list, no tips line, no parked count in the banner |

---

### Task 1: Run names (`run_name`, `Goal.name`, intake prompt)

**Files:**
- Create: `src/phil/run/naming.py`, `tests/run/test_naming.py`
- Modify:
  - `src/phil/contracts/interface.py` (the `Goal` class, after `objective`)
  - `src/phil/prompts/intake.md` (the "Goal fields" list)
  - `tests/chat/test_intake_spec.py`

**Interfaces:**
- **Produces:**
  - `phil.run.naming`:
    - `MAX_CHARS = 32`, `SLUG: re.Pattern`, `STOP_WORDS: frozenset[str]`
    - `normalise(name: str) -> str`
    - `built_name(objective: str, run_id: str) -> str`
    - `run_name(goal: Goal, run_id: str) -> str`
  - `Goal.name: str | None = None`

- [ ] **Step 1: Write the failing tests** (`tests/run/test_naming.py`)

```python
import pytest

from phil.contracts import Goal
from phil.run.naming import MAX_CHARS, SLUG, built_name, run_name

OBJECTIVE = "Add a CTA section below the newsletter form"


def named(name, objective=OBJECTIVE):
    return Goal(objective=objective, name=name)


def test_a_valid_intake_name_is_kept():
    assert run_name(named("newsletter-cta"), "r-4f1c") == "newsletter-cta"


@pytest.mark.parametrize("raw,expected", [
    ("  Newsletter CTA ", "newsletter-cta"),
    ("signup_form_fix", "signup-form-fix"),
    ("newsletter--cta", "newsletter-cta"),
    ("Newsletter _ CTA", "newsletter-cta"),
])
def test_intake_names_are_normalised(raw, expected):
    assert run_name(named(raw), "r-4f1c") == expected


@pytest.mark.parametrize("bad", [None, "", "cta", "a-b-c-d", "newsletter-cta!", "-cta-", "x" * 20 + "-" + "y" * 20])
def test_a_bad_intake_name_is_replaced_by_the_built_one(bad):
    assert run_name(named(bad), "r-4f1c") == "cta-section-below"


def test_stop_words_are_dropped_and_three_words_kept():
    assert built_name("Add a subtract function to calc", "r-1") == "subtract-function-calc"


def test_too_few_words_left_takes_the_first_words_of_the_objective():
    assert built_name("Fix the CSS", "r-1") == "fix-the-css"


def test_punctuation_is_removed_from_words():
    assert built_name("Update README.md, please!", "r-1") == "readmemd-please"


def test_no_usable_words_gives_the_run_id_name():
    assert run_name(Goal(objective="修复 登录"), "r-4f1c") == "run-4f1c"
    assert built_name("the a", "r-9a0b") == "the-a"  # only stop words: the objective's own first words


def test_a_long_name_is_cut_at_a_hyphen():
    assert built_name("Add documentation translations everywhere", "r-1") == "documentation-translations"


def test_a_word_too_long_to_cut_gives_the_run_id_name():
    assert built_name("x" * 40 + " " + "y" * 3, "r-4f1c") == "run-4f1c"


@pytest.mark.parametrize("objective", [
    OBJECTIVE, "Fix the CSS", "修复 登录", "Add documentation translations everywhere", "Make it so, number one",
])
def test_every_name_is_a_slug(objective):
    name = run_name(Goal(objective=objective), "r-4f1c")
    assert SLUG.match(name) and len(name) <= MAX_CHARS
```

Add to `tests/chat/test_intake_spec.py`:

```python
def test_goal_name_is_optional_so_old_outputs_still_validate():
    assert Goal(objective="Add a map").name is None
    assert Goal.model_validate({"objective": "Add a map"}).name is None
    assert Goal(objective="Add a map", name="map-section").name == "map-section"


def test_intake_prompt_asks_for_a_name():
    prompt = load_prompt(get_spec("intake"))
    assert "- `name`: 2 to 3 lowercase keywords naming the goal, joined by hyphens" in prompt
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/run/test_naming.py tests/chat/test_intake_spec.py -q -n 0`
Expected: FAIL. `ModuleNotFoundError: phil.run.naming`, and `Goal` has no attribute `name`.

- [ ] **Step 3: Implement**

`src/phil/contracts/interface.py`, in `class Goal`, right after `objective: str`:

```python
    name: str | None = Field(
        default=None,
        description="2 to 3 lowercase keywords naming the goal, joined by hyphens, e.g. `newsletter-cta`. "
        "Leave null while `open_questions` is non-empty.",
    )
```

`src/phil/prompts/intake.md`, as the line right after the `objective` bullet:

```markdown
- `name`: 2 to 3 lowercase keywords naming the goal, joined by hyphens, e.g. `newsletter-cta`. Leave null while `open_questions` is non-empty.
```

`src/phil/run/naming.py`:

```python
"""Run names (spec 2026-10-16 §3.1): a short slug that tells runs apart on screen. Display only:
the run ID stays the run's identity, and naming never fails a goal or a run (a bad name is
replaced, never rejected)."""

import re

from phil.contracts import Goal

MAX_CHARS = 32
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){1,2}$")
STOP_WORDS = frozenset(
    "a an the to of for and or in on with add make create update fix change new some our my".split()
)


def normalise(name: str) -> str:
    """Intake's name trimmed and lower-cased, spaces and underscores as hyphens, repeats collapsed."""
    slug = re.sub(r"[\s_]+", "-", name.strip().lower())
    return re.sub(r"-{2,}", "-", slug)


def _joined(words: list[str]) -> str | None:
    """`words` joined by hyphens, dropping words from the end until it fits in MAX_CHARS; None once
    fewer than two are left (the pattern needs two)."""
    while len(words) >= 2:
        slug = "-".join(words)
        if len(slug) <= MAX_CHARS:
            return slug
        words = words[:-1]
    return None


def built_name(objective: str, run_id: str) -> str:
    """The name built from the objective: its first three words that aren't stop words; its own
    first words when fewer than two are left; `run-<id>` when even that gives fewer than two."""
    words = [word for word in (re.sub(r"[^a-z0-9]", "", raw) for raw in objective.lower().split()) if word]
    kept = [word for word in words if word not in STOP_WORDS]
    slug = _joined(kept[:3] if len(kept) >= 2 else words[:3])
    return slug or f"run-{run_id.removeprefix('r-')}"


def run_name(goal: Goal, run_id: str) -> str:
    """The name stored for a run started from `goal`: intake's name, normalised, when it is a
    slug of at most MAX_CHARS characters; otherwise the built name."""
    if goal.name:
        slug = normalise(goal.name)
        if len(slug) <= MAX_CHARS and SLUG.match(slug):
            return slug
    return built_name(goal.objective, run_id)
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/run/test_naming.py tests/chat/test_intake_spec.py tests/test_contracts.py tests/test_contract_descriptions.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q` (about 6 minutes). Expected: PASS.

Message (`.git/phil-commit-msg.txt`):

```
Runs: name a run from its goal (run_name, Goal.name)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/run/naming.py src/phil/contracts/interface.py src/phil/prompts/intake.md tests/run/test_naming.py tests/chat/test_intake_spec.py
git commit -F .git/phil-commit-msg.txt
```

---

### Task 2: Store the name (`runs.name`, `create_run`, `prepare_run`)

**Files:**
- Modify:
  - `src/phil/store/db.py` (`MIGRATIONS`)
  - `src/phil/store/runs.py` (`RunRecord`, `create_run`)
  - `src/phil/run/launch.py` (`prepare_run`)
  - `src/phil/chat/controller.py` (`_launch`, the `prepare_run(...)` call)
- Test:
  - `tests/store/test_db.py`
  - `tests/store/test_runs.py`
  - `tests/run/test_launch.py`
  - `tests/chat/test_controller.py`

**Interfaces:**
- **Consumes:** `run_name(goal, run_id)` (Task 1).
- **Produces:**
  - `RunRecord.name: str | None = None` (the last field)
  - `create_run(..., name: str | None = None)`
  - `prepare_run(info, plan, base_sha, *, chat_id=None, overrides=(), depth="full", goal: Goal | None = None) -> RunRecord`
  - Runs the chat starts are named. With the test goal `goal()` ("Add subtract to calc"), the name is `subtract-calc`.

- [ ] **Step 1: Write the failing tests**

`tests/store/test_db.py` (add `from phil.store.runs import get_run` to the imports):

```python
def test_the_name_migration_runs_on_an_existing_database(tmp_path, monkeypatch):
    path = tmp_path / "phil.db"
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS[:-1])  # the database as it was before runs.name
    old = connect(path)
    old.execute(
        "INSERT INTO runs (run_id, keyword, base_sha, branch, worktree, state, tasks_total, created_at, updated_at)"
        " VALUES ('r-0001', 'CALC', 'abc', 'phil/r-0001', '/wt', 'pending', 1, 't', 't')"
    )
    old.close()
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS)
    conn = connect(path)
    assert user_version(conn) == len(MIGRATIONS)
    assert "name" in [row["name"] for row in conn.execute("PRAGMA table_info(runs)")]
    assert get_run(conn, "r-0001").name is None
```

`tests/store/test_runs.py`:

```python
def test_create_run_stores_the_name(conn):
    run = create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/tmp/wt"), tasks_total=1,
                     name="map-section")
    assert run.name == "map-section" and get_run(conn, "r-0001").name == "map-section"
    unnamed = create_run(conn, run_id="r-0002", keyword="MAPS", base_sha="abc", worktree=Path("/tmp/wt"), tasks_total=1)
    assert unnamed.name is None
```

`tests/run/test_launch.py` (add `from phil.contracts import Goal`):

```python
def test_prepare_run_names_a_run_from_its_goal(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha, goal=Goal(objective="Add subtract to calc"))
    assert record.name == "subtract-calc"
    assert prepare_run(info, calc_plan(), info.head_sha).name is None  # `phil run`: a plan file, no goal
```

`tests/chat/test_controller.py`:

```python
def test_a_chat_run_is_named_from_its_goal(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    assert runs[0].name == "subtract-calc"
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/store/test_db.py tests/store/test_runs.py tests/run/test_launch.py tests/chat/test_controller.py -q -n 0`
Expected: FAIL: `create_run() got an unexpected keyword argument 'name'`, `prepare_run() … 'goal'`, and `RunRecord` has no `name`.

- [ ] **Step 3: Implement**

- **`src/phil/store/db.py`:** append as the last `MIGRATIONS` entry:

```python
    "ALTER TABLE runs ADD COLUMN name TEXT",  # display only (spec 2026-10-16 §3.2); NULL for older runs
```

- **`src/phil/store/runs.py`:**
  - Add as `RunRecord`'s last field:

```python
    name: str | None = None  # display only (spec 2026-10-16 §3); NULL for runs before it
```

  - Add `name: str | None = None` as `create_run`'s last parameter, and replace its `INSERT` with:

```python
    conn.execute(
        "INSERT INTO runs (run_id, keyword, base_sha, branch, worktree, state, tasks_total,"
        " story_ref, created_at, updated_at, chat_id, base_branch, config_overrides, depth, name)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id, keyword, base_sha, branch, str(worktree), tasks_total, story_ref, now, now, chat_id, base_branch,
            config_overrides, depth, name,
        ),
    )
```

- **`src/phil/run/launch.py`:**
  - Imports: `from phil.contracts import Goal, Plan` and `from phil.run.naming import run_name`.
  - Signature and docstring:

```python
def prepare_run(
    info: RepoInfo,
    plan: Plan,
    base_sha: str,
    *,
    chat_id: str | None = None,
    overrides: Sequence[str] = (),
    depth: str = "full",
    goal: Goal | None = None,
) -> RunRecord:
    """Record a pending run; its worker reloads the config with the same `--set` `overrides`. A run
    started from a `goal` is named from it (run_name; the fallback needs the ID allocated here)."""
```

  - In the `create_run(...)` call, after `depth=depth,`, add:

```python
            name=run_name(goal, run_id) if goal is not None else None,
```

- **`src/phil/chat/controller.py`, `_launch`:** the call becomes:

```python
            record = prepare_run(
                self.info, plan, base_sha, chat_id=self.session.id, overrides=(*self._config_overrides, pin),
                depth=depth, goal=self._goal,
            )
```

`phil run` (`cli/main.py`) and the bench harness don't change (Ruling R4).

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/store tests/run/test_launch.py tests/chat/test_controller.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Expected: PASS. Nothing displays the name yet, so no text changes.

```
Runs: store each run's name (runs.name)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/store/db.py src/phil/store/runs.py src/phil/run/launch.py src/phil/chat/controller.py tests/store/test_db.py tests/store/test_runs.py tests/run/test_launch.py tests/chat/test_controller.py
git commit -F .git/phil-commit-msg.txt
```

---

### Task 3: Labels (`run_label`, the chat's mentions, `phil show`/`/show`/`phil runs`)

**Files:**
- Create: `src/phil/ui/runs.py`, `tests/ui/test_runs_label.py`, `tests/ui/test_runs_view.py`
- Modify:
  - `src/phil/chat/state.py` (`ChatState`)
  - `src/phil/chat/controller.py` (every rendered mention; table below)
  - `src/phil/ui/show_view.py` (`render_show` header)
  - `src/phil/ui/runs_view.py` (`render_runs` run column)
- Test:
  - `tests/chat/test_state.py`
  - `tests/chat/test_controller_run.py`
  - `tests/ui/test_show_view.py`
  - `tests/chat/conftest.py` (a helper)
- Update existing text pins in:
  - `tests/chat/test_controller_run.py`
  - `tests/chat/test_controller_callouts.py`
  - `tests/chat/test_controller_pr.py`
  - `tests/chat/test_quick_flow.py`
  - `tests/chat/test_controller.py`

**Interfaces:**
- **Consumes:** `RunRecord.name` (Task 2).
- **Produces:**
  - `phil.ui.runs.label_for(run_id: str, name: str | None, *, first: bool) -> str`
  - `phil.ui.runs.run_label(record, *, first: bool) -> str`, where `record` is anything with `.run_id` and `.name`
  - `ChatState.labelled_runs: set[str]` and `ChatState.mention(run_id: str, name: str | None) -> str`
  - `ChatController._label(run_id) -> str`, which marks the run as mentioned
  - `ChatController._short_label(run_id) -> str`, which never marks and returns the bare name or the ID
  - `ChatController._run_name(run_id) -> str | None`
  - Test helper `tests.chat.conftest.label_of(repo, run_id, *, first=False) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_runs_label.py`:

```python
from types import SimpleNamespace

from phil.ui.runs import label_for, run_label


def test_first_and_later_mentions():
    assert label_for("r-4f1c", "newsletter-cta", first=True) == "newsletter-cta (r-4f1c)"
    assert label_for("r-4f1c", "newsletter-cta", first=False) == "newsletter-cta"


def test_a_run_with_no_name_is_its_id():
    for name in (None, ""):
        assert label_for("r-4f1c", name, first=True) == "r-4f1c"
        assert label_for("r-4f1c", name, first=False) == "r-4f1c"


def test_run_label_reads_a_record():
    assert run_label(SimpleNamespace(run_id="r-1", name="map-section"), first=True) == "map-section (r-1)"
```

`tests/chat/test_state.py`:

```python
def test_a_chat_names_a_run_in_full_once():
    state = ChatState()
    assert state.mention("r-1", "map-section") == "map-section (r-1)"
    assert state.mention("r-1", "map-section") == "map-section"
    assert state.labelled_runs == {"r-1"}
    assert state.mention("r-2", None) == "r-2"
```

(`ChatState` is already imported there; if not, add `from phil.chat.state import ChatState`.)

`tests/chat/conftest.py` (imports: `from phil.repo import resolve_repo`, `from phil.store.db import connect`, `from phil.store.paths import ProjectPaths`, `from phil.store.runs import get_run`, `from phil.ui.runs import run_label`; skip any that are already there):

```python
def label_of(repo, run_id: str, *, first: bool = False) -> str:
    """How the chat names `run_id` (spec 2026-10-16 §3.3), read from the store."""
    conn = connect(ProjectPaths(resolve_repo(repo).slug).db_path)
    try:
        return run_label(get_run(conn, run_id), first=first)
    finally:
        conn.close()
```

`tests/chat/test_controller_run.py`:

```python
def test_a_run_is_named_in_full_once_then_by_its_name(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y", escalate()], FULL_SCRIPT)
    run = runs[0]
    assert run.name == "subtract-calc"
    assert f"Run subtract-calc ({run.run_id}) started in the background" in text
    assert "⏸ subtract-calc needs you: CALC-001 failed 3 attempts" in text
    assert f"phil attach {run.run_id}" in text  # command hints keep the ID
    assert text.count(f"subtract-calc ({run.run_id})") == 1
```

`tests/ui/test_show_view.py` (add `from phil.contracts import Goal`):

```python
def test_render_show_names_the_run_and_its_id(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha, goal=Goal(objective="Add subtract to calc"))
    paths = ProjectPaths(info.slug)
    console = make_console(record=True, width=160)
    render_show(console, connect(paths.db_path), paths, record.run_id)
    assert f"Run subtract-calc ({record.run_id}) · CALC" in console.export_text()
```

`tests/ui/test_runs_view.py`:

```python
from pathlib import Path

from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run
from phil.ui.runs_view import render_runs
from phil.ui.theme import make_console


def test_phil_runs_lists_the_name_and_the_id_and_an_old_run_by_its_id(phil_home):
    conn = connect(ProjectPaths("demo-12345678").db_path)
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=Path("/wt"), tasks_total=1,
               name="subtract-calc")
    create_run(conn, run_id="r-0002", keyword="CALC", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    console = make_console(record=True, width=160)
    render_runs(console, conn)
    text = console.export_text()
    assert "subtract-calc (r-0001)" in text
    assert "r-0002" in text and "None" not in text
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/ui/test_runs_label.py tests/ui/test_runs_view.py tests/ui/test_show_view.py tests/chat/test_state.py tests/chat/test_controller_run.py::test_a_run_is_named_in_full_once_then_by_its_name -q -n 0`
Expected: FAIL. `No module named 'phil.ui.runs'`, `ChatState` has no `mention`, and the texts still show bare IDs.

- [ ] **Step 3: Implement the labels**

`src/phil/ui/runs.py`:

```python
"""How screens name a run (spec 2026-10-16 §3.3): `newsletter-cta (r-4f1c)` on its first mention
in a chat, `newsletter-cta` after that, and the ID for a run with no name. Command hints always
print the ID (`phil resume r-4f1c`), never a label."""

from typing import Protocol


class Named(Protocol):
    run_id: str
    name: str | None


def label_for(run_id: str, name: str | None, *, first: bool) -> str:
    if not name:
        return run_id
    return f"{name} ({run_id})" if first else name


def run_label(record: Named, *, first: bool) -> str:
    return label_for(record.run_id, record.name, first=first)
```

`src/phil/chat/state.py`:
- Import `from phil.ui.runs import label_for` (a dependency-free module, so there's no cycle).
- In `ChatState.__init__`, add `self.labelled_runs: set[str] = set()  # runs this chat has named in full`.
- Add:

```python
    def mention(self, run_id: str, name: str | None) -> str:
        """The run's label for a line printed now: `name (id)` the first time this chat names it,
        then `name` (the ID when it has none)."""
        with self._lock:
            first = run_id not in self.labelled_runs
            self.labelled_runs.add(run_id)
        return label_for(run_id, name, first=first)
```

`src/phil/ui/show_view.py`:
- Import `from phil.ui.runs import run_label`.
- In `render_show`'s header, replace `Run [phil.id]{escape(run_id)}[/]` with `Run [phil.id]{escape(run_label(record, first=True))}[/]`.

`src/phil/ui/runs_view.py`:
- Import `run_label`.
- Make the first table cell `f"[phil.id]{escape(run_label(run, first=True))}[/]"`.

- [ ] **Step 4: Implement the chat's mentions** (`src/phil/chat/controller.py`)

Import `from phil.ui.runs import label_for`. In `__init__`, right after `self._run_id: str | None = None`, add:

```python
        self._run_names: dict[str, str | None] = {}  # run id -> its stored name, read once (it never changes)
```

Add next to `_refresh_place`:

```python
    def _run_name(self, run_id: str) -> str | None:
        """The run's stored name; None when it has none or its row can't be read (not cached then)."""
        if run_id not in self._run_names:
            try:
                record = get_run(self.conn, run_id)
            except Exception:
                logger.debug("couldn't read the name of %s", run_id, exc_info=True)
                return None
            self._run_names[run_id] = record.name if record is not None else None
        return self._run_names[run_id]

    def _label(self, run_id: str | None) -> str:
        """The run as a printed line names it (spec 2026-10-16 §3.3): `name (id)` the first time in
        this chat, then `name`; the ID for a run with no name."""
        return self.state.mention(run_id, self._run_name(run_id)) if run_id else ""

    def _short_label(self, run_id: str | None) -> str:
        """The run's name (its ID when unnamed) for redrawn text, such as callout titles and the
        folded prompt, which must not change between redraws (Ruling R5)."""
        return label_for(run_id, self._run_name(run_id), first=False) if run_id else ""
```

In `_launch`, right after `prepare_run(...)` succeeds and before `run_id = escape(record.run_id)`, add `self._run_names[record.run_id] = record.name`. Then add `label = escape(self._label(record.run_id))` after `run_id = escape(record.run_id)`.

Replace each mention as follows. Each "Today" fragment is unique in the file. Keep IDs in every command hint.

| Where | Today | Becomes |
|---|---|---|
| "This chat is following run …" (two places: `_input`'s running stage and the forced-goal `RUN_STAGES` branch; replace both) | `{escape(self._run_id or '')}` | `{escape(self._label(self._run_id))}` |
| `_begin_goal` | `f"[phil.muted]Run {rid} is left as {state}; …"` | add `label = escape(self._label(self._run_id))` after `rid = …`, and print `f"[phil.muted]Run {label} is left as {state}; \`phil resume {rid}\` still continues it.[/]"` |
| `_launch`, spawn failed | `f"[phil.error]Run {run_id} was created but …"` | `f"[phil.error]Run {label} was created but …"` (the `phil resume {run_id}` part unchanged) |
| `_launch`, started | `f"Run [phil.id]{run_id}[/] started in the background …"` | `f"Run [phil.id]{label}[/] started in the background …"` (the `phil attach {run_id}` part unchanged) |
| `_follow` except | `f"[phil.warn]Couldn't follow run {escape(run_id)} here …"` | `{escape(self._label(run_id))}` in that first place only |
| `_closing` | `f"Run [phil.id]{escape(self._run_id)}[/] keeps working. "` | `f"Run [phil.id]{escape(self._label(self._run_id))}[/] keeps working. "` |
| `_on_budget_warning` | `budget_warning_line(self._run_id, **data)` | `budget_warning_line(self._label(self._run_id), **data)` (Ruling R25) |
| `_on_run_paused` | `f"[phil.warn]⏸ {escape(self._run_id)} needs you: …"` | `f"[phil.warn]⏸ {escape(self._label(self._run_id))} needs you: …"` |
| `_on_run_resumed` | `answered_elsewhere(self._run_id)` and `f"[phil.muted]{escape(self._run_id)} resumed.[/]"` | `answered_elsewhere(self._label(self._run_id))` and `f"[phil.muted]{escape(self._label(self._run_id))} resumed.[/]"` |
| `_resume_run` | `The resume worker for {escape(run_id)}` and `Resuming [phil.id]{escape(run_id)}[/]` | `{escape(self._label(run_id))}` in both |
| `_run_notice` | `rid = escape(run_id)`, then `Run {rid}` in five messages | add `label = escape(self._label(run_id))`; every `Run {rid}` becomes `Run {label}`; `phil diff {rid}` unchanged |
| `_on_worker_lost` | `The worker for {escape(self._run_id)}` | `The worker for {escape(self._label(self._run_id))}` |
| `_on_watch_error` | `f"[phil.muted]Live updates for {rid} are failing …"` | `f"[phil.muted]Live updates for {escape(self._label(self._run_id))} are failing …"` (`phil attach {rid}` unchanged) |
| `_resume_command` | `Continuing [phil.id]{escape(run_id)}[/]` | `Continuing [phil.id]{escape(self._label(run_id))}[/]` |
| PR offer (`f"Open a PR for [phil.id]{escape(run_id)}[/] → …"`) | | `{escape(self._label(run_id))}` |
| `_open_pr` | `f"Opening a PR for [phil.id]{escape(run_id)}[/]…"` | `{escape(self._label(run_id))}` |
| `_build_decision`, confirm_pr | `pr_decision(self._pr_offer, self._pr_force)` | `pr_decision(self._short_label(self._pr_offer), self._pr_force)` |
| `_pause_decision` | `pause_decision(self._run_id, pause)` | `pause_decision(self._short_label(self._run_id), pause)` |
| `_folded_prompt` | `reminder = f"⏸ {self._run_id} needs you"` | `reminder = f"⏸ {self._short_label(self._run_id)} needs you"` |
| `_reopen` | `f"[phil.muted]Following run {escape(self._run_id)}.[/]"` | `f"[phil.muted]Following run {escape(self._label(self._run_id))}.[/]"` |

`_decline_pr` (`phil pr …`) and the "Fix that, then `phil pr …`" line keep the ID.

- [ ] **Step 5: Update the existing text pins**

Runs started by the chat are now named (`subtract-calc`; the quick tests' goal gives its own name), and the start line is their first mention. So every later line uses the bare name.

- **The rule:** in the message part of each assertion below, replace the run ID with `run_label(<record>, first=False)` (import it from `phil.ui.runs`). Where the test only holds an ID, use `label_of(calc_repo, <id>)` from `tests.chat.conftest`; add `calc_repo` to the test's parameters if it's missing.
- **Start lines and `/show`:** these use `first=True`.
- **Command hints:** keep the ID.

| File:line (today) | New assertion |
|---|---|
| `test_controller_run.py:105` | `f"⏸ {run_label(runs[0], first=False)} needs you: CALC-001 failed 3 attempts"` |
| `test_controller_run.py:128` | `f"{run_label(runs[0], first=False)} resumed."` |
| `test_controller_run.py:259` | `f"✓ Run {run_label(runs[0], first=False)} completed · 1/1 tasks · 0 tokens · $0.00"` |
| `test_controller_run.py:281` | `f"Run {run_label(aborted, first=False)} was aborted."` |
| `test_controller_run.py:301` | `f"Run {run_label(runs[0], first=False)} failed: worker failed: boom"` |
| `test_controller_run.py:313` | `f"The worker for {run_label(runs[0], first=False)} stopped responding. Continue it with /resume."` |
| `test_controller_run.py:331` | `f"Live updates for {run_label(runs[0], first=False)} are failing (OSError: disk gone); retrying. \`phil attach {run_id}\` also works."` |
| `test_controller_run.py:332` and `:627` | `f"✓ Run {run_label(runs[0], first=False)} completed"` |
| `test_controller_run.py:454` and `:460` | `f"Run {run_label(runs[0], first=False)} keeps working…"` (rest unchanged) |
| `test_controller_run.py:610` | `f"Run {run_label(runs[0], first=False)} is left as failed; \`phil resume {run_id}\` still continues it."` |
| `test_controller_run.py:763` | `f"Run {run_label(runs[0], first=True)} · CALC · full · completed"` |
| `test_controller_callouts.py:142` | `folded[1].startswith(f"⏸ {run_label(runs[0], first=False)} needs you · /answer to choose")` |
| `test_controller_callouts.py:187` and `:587` | `f"{run_label(runs[0], first=False)} was answered elsewhere."` |
| `test_controller_callouts.py:238` | `f"Run {run_label(runs[0], first=False)} failed."` |
| `test_controller_callouts.py:314` | `text.index(f"Run {run_label(runs[0], first=True)} started")` |
| `test_controller_callouts.py:339` | `f"Resuming {run_label(runs[0], first=False)} with retry."` |
| `test_controller_pr.py:49` | `f"Open a PR for {run_label(runs[0], first=False)} → main?"` |
| `test_controller_pr.py:85` | `f"Opening a PR for {run_label(runs[0], first=False)}…"` |
| `test_controller_pr.py:212` and `:214` | `f"Run {run_label(runs[0], first=False)} finished with …"` / `f"Run {run_label(runs[0], first=False)} completed" not in text` |
| `test_quick_flow.py:450` | `f"Resuming {run_label(run, first=False)} with full."` |
| `test_quick_flow.py:471`, `:559` | `f"Run {label_of(calc_repo, quick_id)} was aborted."` |
| `test_quick_flow.py:576` | `f"Run {label_of(calc_repo, spawned[0][0])} was aborted."` |
| `test_controller.py:602` | `f"Run {run_label(runs[0], first=True)} started in the background from {sha7}."` |
| `test_controller.py:632` | `f"This chat is following run {run_label(runs[0], first=False)}."` |

`tests/chat/test_decision.py` and `tests/chat/test_terminal.py` don't change, because `pause_decision` keeps its signature (it is passed the short label). `tests/cli/test_show_command.py` doesn't change either, because its runs come from `prepare_run` with no goal and stay unnamed.

- [ ] **Step 6: Run the tests and see them pass**

Run: `uv run pytest tests/ui tests/chat -q -n 0`
Expected: PASS. If another assertion fails only because a run ID became a label, apply the Step 5 rule and add its file to the commit.

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`. Expected: PASS.

```
UI: name runs on screen (run_label; the ID stays in commands)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/ui/runs.py src/phil/chat/state.py src/phil/chat/controller.py src/phil/ui/show_view.py src/phil/ui/runs_view.py tests/ui/test_runs_label.py tests/ui/test_runs_view.py tests/ui/test_show_view.py tests/chat/test_state.py tests/chat/conftest.py tests/chat/test_controller_run.py tests/chat/test_controller_callouts.py tests/chat/test_controller_pr.py tests/chat/test_quick_flow.py tests/chat/test_controller.py
git commit -F .git/phil-commit-msg.txt
```

---

### Task 4: Status bar and live rows

**Files:**
- Modify:
  - `src/phil/chat/state.py` (`RunView`)
  - `src/phil/chat/watcher.py` (`run_progress` carries `name` and `title`)
  - `src/phil/chat/controller.py` (`_on_run_progress`)
  - `src/phil/ui/toolbar.py` (`_Segment`, `_segments`, `render_toolbar`, `render_live_rows`; delete `task_dots`, `MAX_DOTS`, `format_tokens`)
- Test:
  - `tests/ui/test_toolbar.py`
  - `tests/chat/test_watcher.py`
  - `tests/chat/test_controller_status.py`

**Interfaces:**
- **Consumes:** `RunRecord.name` (Task 2).
- **Produces:**
  - `RunView.name: str | None = None` and `RunView.title: str | None = None` (the last fields)
  - `run_progress` data gains `"name"` and `"title"`
  - `toolbar.TITLE_FLOOR = 12`
  - `_Segment.title: int | None = None` (the index of the fragment holding the task title)

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_toolbar.py`:
- **Imports:** `from dataclasses import replace`; drop `format_tokens` and `task_dots`.
- **Delete:** `test_format_tokens`, `test_task_dots_pulse_and_pause` and `test_twelve_tasks_show_twelve_dots`.
- **Replace:** `RUN`, `FULL` and the tests named below.

```python
RUN = RunView(run_id="r-4f2a", keyword="calc", node="implement", tasks_done=1, tasks_total=3, started=0.0,
              name="calc-subtract", title="Add a subtract function")
FULL = ToolbarView(repo="calc", branch="phil/r-4f2a", run=RUN, model=("low", "gemini-3.8-flash"),
                   tokens=182_000, run_cost=(0.41, "reported"), budget_usd=1.0)


def test_run_in_progress_full_width():
    assert text(FULL) == ("calc @ phil/r-4f2a │ calc-subtract · task 2 of 3 · Add a subtract function │ 1m 04s │ "
                          "$0.41 / $1.00")


def test_a_run_shows_no_dots_model_tokens_or_node():
    out = text(FULL)
    assert not any(gone in out for gone in ("●", "◉", "gemini", "tok", "implement"))


def test_an_unnamed_run_shows_its_id():
    assert "calc @ phil/r-4f2a │ r-4f2a · task 2 of 3 · " in text(replace(FULL, run=replace(RUN, name=None)))


def test_planning_no_title_and_every_task_done():
    planning = replace(FULL, run=replace(RUN, tasks_done=0, tasks_total=0, title=None))
    assert "│ calc-subtract · planning │" in text(planning)
    assert "│ calc-subtract · task 2 of 3 │" in text(replace(FULL, run=replace(RUN, title=None)))
    assert "│ calc-subtract · Reviewing │" in text(replace(FULL, run=replace(RUN, tasks_done=3, node="review")))


def test_paused_shows_only_the_place_and_the_notice():
    paused = replace(FULL, paused=True)
    assert text(paused) == "calc @ phil/r-4f2a │ ⏸ calc-subtract needs you"
    assert text(replace(paused, btw_pending=1, parked=2)).endswith("│ ⏸ calc-subtract needs you │ /btw ×1 │ 2 parked")


def test_no_budget_shows_cost_alone():
    view = ToolbarView(repo="calc", branch="b", run=RUN, run_cost=(0.41, "estimated"), budget_usd=0.0)
    assert text(view).endswith("│ ~$0.41")


def test_the_title_shortens_before_anything_drops():
    assert text(FULL, width=90) == ("calc @ phil/r-4f2a │ calc-subtract · task 2 of 3 · Add a subtra… │ 1m 04s │ "
                                    "$0.41 / $1.00")


def test_drop_order():
    # The title first shortens to 12 cells; then repo → elapsed → cost drop; the run segment is last.
    assert text(FULL, width=80) == "calc-subtract · task 2 of 3 · Add a subtr… │ 1m 04s │ $0.41 / $1.00"
    assert text(FULL, width=60) == "calc-subtract · task 2 of 3 · Add a subtr… │ $0.41 / $1.00"
    assert text(FULL, width=50) == "calc-subtract · task 2 of 3 · Add a subtr…"
    assert text(FULL, width=40).startswith("calc-subtract · task 2 of 3")


def test_extras_drop_first():
    view = replace(FULL, btw_pending=2, parked=3)
    out = text(view, width=90)  # the title is at its floor, then /btw and parked go, before the repo
    assert "parked" not in out and "/btw" not in out and out.startswith("calc @ phil/r-4f2a │ ")


def test_never_wraps_and_pause_survives():
    paused = replace(FULL, paused=True)
    for width in (20, 30, 40, 60, 80, 120):
        assert cell_len(text(FULL, width=width)) <= width - 1
        assert "⏸ calc-subtract needs you" in text(paused, width=max(width, 27))


def test_a_wide_title_and_a_long_name_never_overflow():
    run = replace(RUN, name="internationalisation-settings", title="日本語のタイトルを追加する" * 3)
    for paused in (False, True):
        view = replace(FULL, run=run, paused=paused, btw_pending=1, parked=4)
        for width in range(1, 121):
            assert cell_len(text(view, width=width)) <= max(width - 1, 0)


def test_a_cut_pause_notice_keeps_its_warning_style():
    paused = ToolbarView(repo="calc", branch="b", run=RUN, paused=True, run_cost=(0.41, "reported"), budget_usd=1.0)
    frags = render_toolbar(paused, 64.0, 12)
    assert toolbar_text(frags).endswith("…") and cell_len(toolbar_text(frags)) <= 11
    assert toolbar_text(frags).startswith("⏸ calc-sub")
    assert all(style == "class:phil.warn" for style, _ in frags)
```

- In `test_dropping_progress_drops_its_orphaned_elapsed`, replace `assert "r-4f2a" not in out` with `assert "calc-subtract" not in out`.
- In `test_one_agent_is_exactly_todays_row`, rename it to `test_the_main_row_names_the_agent_not_the_task`, with the assertion `assert len(out) == 1 and out[0][2:] == "implementer · run pytest -q · 2s"`.
- In `test_a_cut_row_keeps_each_fragments_style`, change `"CALC-002" in f[1]` to `"implementer" in f[1]`.

`tests/chat/test_watcher.py` (add `from phil.contracts import Goal`):

```python
def test_run_progress_carries_the_name_and_the_current_tasks_title(calc_repo):
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    record = prepare_run(info, calc_plan(), info.head_sha, goal=Goal(objective="Add subtract to calc"))
    posted = []
    watcher = RunWatcher(paths, record.run_id, posted.append, alive=lambda r: True, starting=lambda e: False)
    conn = connect(paths.db_path)
    update_run(conn, record.run_id, state="running", current_node="implement")
    watcher.poll_once()
    progress = [e for e in posted if e.kind == "run_progress"][-1]
    assert (progress.data["name"], progress.data["title"]) == ("subtract-calc", "Add subtract")
    update_run(conn, record.run_id, tasks_done=1, current_node="review")
    watcher.poll_once()
    assert [e for e in posted if e.kind == "run_progress"][-1].data["title"] is None  # every task done
```

`tests/chat/test_controller_status.py`: replace the body of `test_status_line_end_to_end` after `text = …`:

```python
    assert controller.info.root.name in text
    assert "subtract-calc · Implementing" in text  # its one task is done; the stage shows (Ruling R7)
    assert "●" not in text and "small-model" not in text
    assert " / $" in text
```

Also update its docstring: "contains the repo name, the run's name and stage, and ' / $' (the budget), but no dots or model".

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/ui/test_toolbar.py tests/chat/test_watcher.py tests/chat/test_controller_status.py -q -n 0`
Expected: FAIL. `RunView` has no `name`, and the bar still shows dots and the model.

- [ ] **Step 3: Implement**

- **`src/phil/chat/state.py`:** add as `RunView`'s last fields:

```python
    name: str | None = None  # the run's display name (spec 2026-10-16 §3); None: show run_id
    title: str | None = None  # the current task's title; None before tasks exist or once all are done
```

- **`src/phil/chat/watcher.py`:**
  - Import `from phil.store.artifacts import ArtifactStore`.
  - In `__init__`, add `self._tasks: list[str] | None = None  # the plan's task titles, read once`.
  - Add:

```python
    def _title(self, done: int) -> str | None:
        """The current task's title (task `done + 1` of the run's saved plan, whitespace collapsed);
        None once every task is done or when the plan can't be read (Ruling R8)."""
        if self._tasks is None:
            try:
                plan = ArtifactStore(self.paths.run_dir(self.run_id)).read_plan()
                self._tasks = [" ".join(task.description.split()) for task in plan.tasks]
            except Exception:
                logger.debug("couldn't read the plan of %s", self.run_id, exc_info=True)
                self._tasks = []
        return self._tasks[done] if 0 <= done < len(self._tasks) else None
```

  - In `poll_once`'s `run_progress` data, add `"name": record.name, "title": self._title(record.tasks_done),`.

- **`src/phil/chat/controller.py`, `_on_run_progress`:** pass `name=data.get("name"), title=data.get("title"),` to `RunView(...)`.

- **`src/phil/ui/toolbar.py`:**
  - Delete `MAX_DOTS`, `format_tokens` and `task_dots`.
  - Add `TITLE_FLOOR = 12  # the task title shortens to this many cells before any segment drops`.
  - Add `title: int | None = None  # the index of the fragment holding the task title` as `_Segment`'s last field.
  - Replace `_segments` and the start of `render_toolbar` with:

```python
def _run_segment(run: RunView) -> _Segment:
    """`name · task 2 of 4 · title`; `name · planning` before there are tasks; the stage label once
    every task is done (Ruling R7). A run with no name shows its ID."""
    frags: Fragments = [("class:phil.id", run.name or run.run_id)]
    if run.tasks_total <= 0:
        frags.append(("", " · planning"))
    elif run.tasks_done >= run.tasks_total:
        node = run.node or "finish"
        frags.append(("", f" · {NODE_LABELS.get(node, node)}"))
    else:
        frags.append(("", f" · task {run.tasks_done + 1} of {run.tasks_total}"))
        if run.title:
            frags += [("", " · "), ("", run.title)]
            return _Segment(P_PROGRESS, frags, title=len(frags) - 1)
    return _Segment(P_PROGRESS, frags)


def _segments(view: ToolbarView, now: float) -> list[_Segment]:
    segs: list[_Segment] = []
    if view.repo:
        segs.append(_Segment(P_REPO, [("", view.repo), ("class:phil.muted", f" @ {view.branch or '?'}")]))
    run = view.run
    if run and view.paused:
        segs.append(_Segment(P_PAUSE, [("class:phil.warn", f"⏸ {run.name or run.run_id} needs you")]))
    elif run:
        segs.append(_run_segment(run))
        segs.append(_Segment(P_ELAPSED, [("", elapsed(now - run.started))]))
        if view.run_cost is not None:
            cost, source = view.run_cost
            shown = format_cost(cost, source)
            if view.budget_usd > 0:
                shown += f" / ${view.budget_usd:.2f}"
            segs.append(_Segment(P_COST, [(budget_style(cost, view.budget_usd), shown)]))
    elif view.step:
        frame = SPINNER[int((now - view.step_started) * 8) % len(SPINNER)]
        label = STEP_LABELS.get(view.step, view.step)
        step = f"{frame} {label} · {elapsed(now - view.step_started)}"
        if view.cancelling:
            step += " (cancelling…)"
        segs.append(_Segment(P_PROGRESS, [("", step)]))
    else:
        segs.append(_Segment(P_PROGRESS, [("class:phil.muted", "Phil · type a goal, or /help")]))
    if not run:
        if view.model:
            tier, name = view.model
            segs.append(_Segment(P_MODEL, [("class:phil.muted", f"{tier}·"), ("", name)]))
        if view.cost is not None:
            segs.append(_Segment(P_COST, [("class:phil.cost", f"chat {format_cost(*view.cost)}")]))
    if view.btw_pending:
        segs.append(_Segment(P_EXTRA, [("class:phil.muted", f"/btw ×{view.btw_pending}")]))
    if view.parked:
        segs.append(_Segment(P_EXTRA, [("class:phil.muted", f"{view.parked} parked")]))
    return segs


def _shorten_title(segs: list[_Segment], limit: int) -> None:
    """Cut the task title by what the line is over `limit`, down to TITLE_FLOOR cells, with …."""
    excess = cell_len(toolbar_text(_join(segs))) - limit
    for seg in segs:
        if excess <= 0 or seg.title is None:
            continue
        style, title = seg.fragments[seg.title]
        cells = cell_len(title)
        if cells > TITLE_FLOOR:
            shorter = _cut_cells(title, max(TITLE_FLOOR, cells - excess))
            seg.fragments[seg.title] = (style, shorter)
            excess -= cells - cell_len(shorter)
```

  - In `render_toolbar`, insert `_shorten_title(segs, limit)` as the first line inside `if width is not None:`, right after `limit = width - 1`. Leave the rest of the drop loop unchanged.
  - In `render_live_rows`, the main row's parts become:

```python
            parts = [p for p in (live.role, live.summary, elapsed(now - live.started)) if p]
```

  - Update `render_toolbar`'s docstring reference: "segments drop (spec 2026-10-16 §4.1 order)".

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/ui/test_toolbar.py tests/chat/test_watcher.py tests/chat/test_controller_status.py tests/chat/test_controller_feed.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Expected: PASS.

```
UI: the status bar tracks the task; live rows name the agent

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/chat/state.py src/phil/chat/watcher.py src/phil/chat/controller.py src/phil/ui/toolbar.py tests/ui/test_toolbar.py tests/chat/test_watcher.py tests/chat/test_controller_status.py
git commit -F .git/phil-commit-msg.txt
```

---

### Task 5: The burst model, the renderer and the rolling view

**Files:**
- Create: `src/phil/ui/bursts.py`, `tests/ui/test_bursts.py`
- Modify:
  - `src/phil/ui/feed_view.py` (`FeedRenderer`; folding moves to `bursts.fold`)
  - `src/phil/ui/toolbar.py` (`MAX_LIVE_LINES = 6`, rolling rows, the cap)
  - `src/phil/chat/state.py` (`ToolbarView.rolling`, `ChatState.set_rolling`)
- Test:
  - `tests/ui/test_feed_view.py`
  - `tests/ui/test_toolbar.py`

**Interfaces:**
- **Produces** (`phil.ui.bursts`):
  - `INDENT`, `READS`, `LOOKS`, `TIMED`, `EDITS`, `ENGINE = "engine"`, `ROLLING_HEAD = 2`, `NAMED_EDITS = 3`, `SHELL_CHARS = 40`
  - `@dataclass(frozen=True) class FeedItem: line: Text; records: tuple[dict, ...]`
  - `@dataclass class Burst: task: str | None; role: str; calls: list[dict]`, with the properties `key` and `number: int | None` (its last call's seq)
  - `key_of(record) -> tuple[str | None, str]`
  - `is_failure(record) -> bool`
  - `fold(ends: list[dict]) -> list[list[dict]]`
  - `group_summary(group) -> str`
  - `call_text(group) -> str`
  - `rolling_lines(burst: Burst | None) -> tuple[str, ...]`
  - `edited_files(calls) -> list[str]`
  - `last_command(calls) -> str | None`
  - `burst_text(burst) -> str`
- **Produces** (`FeedRenderer`):
  - `__init__(self, *, more_hint: bool = True)`
  - `activity(records, width) -> list[FeedItem]`
  - `end_burst(width) -> list[FeedItem]`
  - `rolling() -> tuple[str, ...]`
  - `burst(number: int) -> list[dict] | None`
  - `tool_lines(records, width, cap: int | None = BURST_LINES)` (unchanged otherwise)
  - `_line(record, width, summary=None, style="phil.muted")`
- **Produces** (state): `ToolbarView.rolling: tuple[str, ...] = ()` and `ChatState.set_rolling(lines: tuple[str, ...])`.

- [ ] **Step 1: Write the failing tests**

`tests/ui/test_bursts.py`:

```python
from phil.ui.bursts import Burst, burst_text, edited_files, rolling_lines


def end(seq, tool, summary, result="", ok=True, role="implementer", ms=0, **extra):
    return {"seq": seq, "phase": "end", "task": "CALC-001", "role": role, "tool": tool, "summary": summary,
            "result": result, "ok": ok, "detail": None, "duration_ms": ms, **extra}


def burst(*calls):
    return Burst("CALC-001", "implementer", list(calls))


def test_folded_looks_count_as_their_real_number_and_show_as_one():
    b = burst(end(1, "read_file", "read a.py"), end(2, "read_file", "read b.py"), end(3, "grep", 'grep "x" src'),
              end(4, "run_shell", "run pytest -q", "→ ok", ms=400))
    assert burst_text(b) == "implementer ran 4 tools · ran pytest -q"
    assert rolling_lines(b) == ('    look   a.py · b.py · "x" src', "    run    pytest -q  → ok · 0.4s")


def test_edited_names_three_files_then_a_count():
    edits = [end(i, "edit_file", f"edit f{i}.py", "+1 −0") for i in range(1, 6)]
    b = burst(*edits, end(6, "write_file", "write f1.py", "+3"))
    assert burst_text(b) == "implementer ran 6 tools · edited f1.py, f2.py, f3.py +2"


def test_ran_names_the_last_shell_command_cut_to_40_characters():
    long = "node build.mjs --minify --sourcemap --target=es2022 --outdir=dist"
    b = burst(end(1, "run_shell", "run npm ci"), end(2, "run_shell", f"run {long}"))
    assert burst_text(b) == f"implementer ran 2 tools · ran {long[:39]}…"


def test_neither_is_the_count_alone_and_one_tool_is_singular():
    assert burst_text(burst(end(1, "read_file", "read a.py"))) == "implementer ran 1 tool"


def test_a_failed_edit_is_not_an_edited_file():
    assert edited_files([end(1, "edit_file", "edit a.py", "boom", ok=False)]) == []


def test_three_or_fewer_calls_all_show():
    b = burst(end(1, "edit_file", "edit a.py", "+1 −0"), end(2, "run_shell", "run make", "→ ok"),
              end(3, "edit_file", "edit b.py", "+2 −1"))
    assert rolling_lines(b) == ("    edit   a.py  +1 −0", "    run    make  → ok", "    edit   b.py  +2 −1")


def test_more_shows_the_first_two_a_count_and_the_latest():
    calls = [end(i, "edit_file", f"edit f{i}.py") for i in range(1, 9)]
    assert rolling_lines(burst(*calls)) == ("    edit   f1.py", "    edit   f2.py", "    … 5 more", "    edit   f8.py")
    grown = rolling_lines(burst(*calls, end(9, "edit_file", "edit f9.py")))
    assert grown[2:] == ("    … 6 more", "    edit   f9.py")  # the latest is replaced and N grows


def test_sub_agent_calls_count_but_stay_out_of_the_rolling_view():
    b = burst(end(1, "edit_file", "edit a.py"), end(2, "read_file", "read t.py", sub_id=1, sub="explore"),
              end(3, "task", "sub-agent: explore"))
    assert burst_text(b) == "implementer ran 3 tools · edited a.py"
    assert rolling_lines(b) == ("    edit   a.py", "    sub-agent: explore")


def test_no_burst_has_no_rolling_lines():
    assert rolling_lines(None) == ()
    assert Burst("T", "tester").number is None
```

`tests/ui/test_feed_view.py` (add `from phil.ui.bursts import FeedItem` only if a test uses it):

```python
def test_a_running_burst_prints_nothing_and_rolls_then_one_summary():
    feed = FeedRenderer()
    batch = [end(1, "edit_file", "edit a.py", "+1 −0", ms=0), end(2, "run_shell", "run pytest -q", "→ ok", ms=0)]
    assert feed.activity(batch, 120) == []
    assert feed.rolling() == ("    edit   a.py  +1 −0", "    run    pytest -q  → ok")
    (item,) = feed.end_burst(120)
    assert item.line.plain == "    implementer ran 2 tools · edited a.py  (/more #2)"
    assert [r["seq"] for r in item.records] == [1, 2]
    assert feed.rolling() == () and feed.end_burst(120) == []


def test_another_agent_ends_the_burst():
    feed = FeedRenderer()
    items = feed.activity([end(1, "edit_file", "edit a.py"), end(2, "read_file", "read t.py", role="tester")], 120)
    assert [i.line.plain for i in items] == ["    implementer ran 1 tool · edited a.py  (/more #1)"]
    assert feed.rolling() == ("    read   t.py",)


def test_a_start_record_of_another_agent_ends_the_burst():
    feed = FeedRenderer()
    feed.activity([end(1, "edit_file", "edit a.py")], 120)
    start = {"seq": 2, "phase": "start", "task": "CALC-001", "role": "engine", "tool": "gate", "summary": "gate x"}
    assert [i.line.plain for i in feed.activity([start], 120)] == ["    implementer ran 1 tool · edited a.py  (/more #1)"]


def test_a_failure_prints_at_once_in_the_error_style_and_still_counts():
    feed = FeedRenderer()
    failed = {**end(2, "run_shell", "run pytest -q", "→ 1 failed", "2.txt"), "ok": False}
    (item,) = feed.activity([end(1, "edit_file", "edit a.py"), failed], 120)
    assert item.line.plain.startswith("    run   pytest -q  → 1 failed") and item.line.plain.endswith("#2")
    assert item.line.style == "phil.error"
    assert feed.end_burst(120)[0].line.plain == "    implementer ran 2 tools · edited a.py  (/more #2)"


def test_the_engines_gate_keeps_its_own_line_and_ends_the_burst():
    feed = FeedRenderer()
    gate = end(2, "gate", "gate uv run pytest", "→ passed", role="engine", ms=0)
    items = feed.activity([end(1, "edit_file", "edit a.py"), gate], 120)
    assert [i.line.plain for i in items] == [
        "    implementer ran 1 tool · edited a.py  (/more #1)", "    gate  uv run pytest  → passed",
    ]


def test_closed_bursts_are_kept_for_more():
    feed = FeedRenderer()
    feed.activity([end(1, "edit_file", "edit a.py"), end(2, "edit_file", "edit b.py")], 120)
    feed.end_burst(120)
    assert [r["seq"] for r in feed.burst(2)] == [1, 2]
    assert feed.burst(1) is None


def test_attach_leaves_out_the_more_hint_and_a_narrow_summary_keeps_it():
    quiet = FeedRenderer(more_hint=False)
    quiet.activity([end(1, "edit_file", "edit a.py")], 120)
    assert quiet.end_burst(120)[0].line.plain == "    implementer ran 1 tool · edited a.py"
    narrow = FeedRenderer()
    narrow.activity([end(i, "edit_file", f"edit src/components/f{i}.tsx") for i in range(1, 4)], 120)
    line = narrow.end_burst(40)[0].line.plain
    assert line.endswith("…  (/more #3)") and cell_len(line) <= 39


def test_tool_lines_without_a_cap_list_every_call():
    records = [end(i, "edit_file", f"edit f{i}.py", "+1 −0", f"{i}.txt") for i in range(1, BURST_LINES + 8)]
    assert len(FeedRenderer().tool_lines(records, 120, cap=None)) == BURST_LINES + 7
```

`tests/ui/test_toolbar.py`:

```python
ROLL = ("    edit   a.py", "    edit   b.py", "    … 3 more", "    run    make  → ok")


def test_the_rolling_lines_sit_under_the_main_row():
    out = lines(ToolbarView(run=RUN2, live=MAIN, rolling=ROLL))
    assert out[1:] == list(ROLL)


def test_the_live_area_is_capped_at_six_lines_rolling_lines_first():
    subs = (SubAgent(1, "x", None, 90.0), SubAgent(2, "y", None, 90.0))
    out = lines(ToolbarView(run=RUN2, live=MAIN, rolling=ROLL, subs=subs))
    assert len(out) == 6 and out[1:5] == list(ROLL) and out[5].strip() == "+2 more"


def test_under_a_callout_the_rolling_lines_collapse_without_counting_as_agents():
    text = toolbar_text(render_live_rows(ToolbarView(run=RUN2, live=MAIN, rolling=ROLL), 100.0, max_lines=1))
    assert "\n" not in text and "more" not in text
```

Replace `test_more_than_three_collapses_and_nothing_wraps` with:

```python
def test_more_than_six_collapses_and_nothing_wraps():
    subs = tuple(SubAgent(i, f"job {i}", None, 90.0) for i in range(6))
    view = ToolbarView(run=RUN2, live=MAIN, subs=subs, side=(SideJob("q", 99.0),))
    out = lines(view, width=40)
    assert len(out) == 6 and out[-1].strip() == "+3 more"
    assert all(cell_len(line) <= 39 for line in out)
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/ui/test_bursts.py tests/ui/test_feed_view.py tests/ui/test_toolbar.py -q -n 0`
Expected: FAIL. `No module named 'phil.ui.bursts'`, `FeedRenderer` has no `activity`, and `ToolbarView` has no `rolling`.

- [ ] **Step 3: Implement the model** (`src/phil/ui/bursts.py`)

```python
"""Tool-call bursts (spec 2026-10-16 §5): the tool calls of one agent invocation, shown as a
rolling view while it runs and as one summary line once it ends. Pure: the feed renderer holds
the state. Activity records carry no invocation id, so a burst is one (task, role) run of records
(Ruling R11)."""

from dataclasses import dataclass, field

from rich.cells import cell_len
from rich.text import Text

INDENT = "    "
READS = ("read_file",)
LOOKS = ("read_file", "ls", "glob", "grep")  # consecutive look-only calls by one task and role fold into one
TIMED = ("run_shell", "gate")
EDITS = ("edit_file", "write_file")
ENGINE = "engine"  # the engine's own gate runs: never a burst (Ruling R12)
ROLLING_HEAD = 2  # the calls a long burst keeps at the top of its rolling view
NAMED_EDITS = 3
SHELL_CHARS = 40


@dataclass(frozen=True)
class FeedItem:
    """A feed line and the activity records it stands for (what `/feed <agent>` filters on)."""

    line: Text
    records: tuple[dict, ...]


@dataclass
class Burst:
    task: str | None
    role: str
    calls: list[dict] = field(default_factory=list)  # end records, in order

    @property
    def key(self) -> tuple[str | None, str]:
        return (self.task, self.role)

    @property
    def number(self) -> int | None:
        """What `/more #n` calls it: its last call's seq (Ruling R15)."""
        seq = self.calls[-1].get("seq") if self.calls else None
        return seq if isinstance(seq, int) and not isinstance(seq, bool) else None


def key_of(record: dict) -> tuple[str | None, str]:
    return (record.get("task"), str(record.get("role") or ""))


def is_failure(record: dict) -> bool:
    """An error result, a non-zero exit, a timeout or a refusal: the call's end record says ok=False."""
    return record.get("phase") == "end" and record.get("ok") is False


def fold(ends: list[dict]) -> list[list[dict]]:
    """Consecutive look-only calls by one task and role, as one group. A failed look keeps its own
    group, so its result and #ref are never hidden inside a fold."""
    groups: list[list[dict]] = []
    for record in ends:
        last = groups[-1] if groups else None
        if (last and record.get("tool") in LOOKS and last[-1].get("tool") in LOOKS
                and record.get("ok") is not False and last[-1].get("ok") is not False
                and last[-1].get("task") == record.get("task") and last[-1].get("role") == record.get("role")):
            last.append(record)
        else:
            groups.append([record])
    return groups


def group_summary(group: list[dict]) -> str:
    """One call's summary, or a folded group's: `read a.py · b.py` (all reads) or `look …`."""
    if len(group) == 1:
        return str(group[0].get("summary", ""))
    parts = " · ".join(part for r in group if (part := str(r.get("summary", "")).partition(" ")[2]))
    verb = "read" if all(r.get("tool") in READS for r in group) else "look"
    return f"{verb} {parts}"


def call_text(group: list[dict]) -> str:
    """One rolling-view line: verb, target, result and a timed call's duration (plain text)."""
    record = group[-1]
    summary = group_summary(group)
    verb, _, rest = summary.partition(" ")
    if record.get("tool") == "gate":
        verb = "gate"
    elif cell_len(verb) > 6:  # no short verb (a sub-agent, a fallback summary): the summary alone
        verb, rest = "", summary
    head = f"{INDENT}{verb:<6} " if verb else INDENT
    result = f"  {record['result']}" if record.get("result") else ""
    duration = ""
    if record.get("tool") in TIMED and record.get("duration_ms"):
        duration = f" · {record['duration_ms'] / 1000:.1f}s"
    return f"{head}{rest}{result}{duration}"


def rolling_lines(burst: Burst | None) -> tuple[str, ...]:
    """The main agent's calls so far (sub-agent calls have their own row): all of them up to three
    display items, else the first two, `… N more` and the latest (a folded group is one item)."""
    if burst is None:
        return ()
    own = [call for call in burst.calls if call.get("sub_id") is None]
    items = [call_text(group) for group in fold(own)]
    if len(items) <= ROLLING_HEAD + 1:
        return tuple(items)
    hidden = len(items) - ROLLING_HEAD - 1
    return (*items[:ROLLING_HEAD], f"{INDENT}… {hidden} more", items[-1])


def edited_files(calls: list[dict]) -> list[str]:
    """The files the burst's successful edits and writes touched, in first-touched order."""
    files: list[str] = []
    for call in calls:
        if call.get("tool") in EDITS and call.get("ok") is not False:
            path = str(call.get("summary", "")).partition(" ")[2]
            if path and path not in files:
                files.append(path)
    return files


def last_command(calls: list[dict]) -> str | None:
    for call in reversed(calls):
        if call.get("tool") == "run_shell":
            command = str(call.get("summary", "")).removeprefix("run ").strip()
            if command:
                return command
    return None


def burst_text(burst: Burst) -> str:
    """`implementer ran 9 tools · edited a, b, c +2`, `… · ran <command>`, or the count alone."""
    count = len(burst.calls)
    text = f"{burst.role} ran {count} tool{'' if count == 1 else 's'}"
    files = edited_files(burst.calls)
    if files:
        more = f" +{len(files) - NAMED_EDITS}" if len(files) > NAMED_EDITS else ""
        return f"{text} · edited {', '.join(files[:NAMED_EDITS])}{more}"
    command = last_command(burst.calls)
    if command:
        shown = command if len(command) <= SHELL_CHARS else command[: SHELL_CHARS - 1] + "…"
        return f"{text} · ran {shown}"
    return text
```

- [ ] **Step 4: Implement the renderer** (`src/phil/ui/feed_view.py`)

- **Imports:** `from phil.ui.bursts import ENGINE, INDENT, LOOKS, READS, TIMED, Burst, FeedItem, burst_text, fold, group_summary, is_failure, key_of, rolling_lines`.
- **Remove:** the module's own `INDENT`, `_READS`, `_LOOKS` and `_TIMED`, using the imported names instead. Keep `INDENT` importable from `feed_view`, since it is re-exported by the import.
- **Update the docstring:** "Shared by the chat and `phil attach`; burst state lives here (spec 2026-10-16 §5, §7)".

```python
class FeedRenderer:
    def __init__(self, *, more_hint: bool = True) -> None:
        self._task_started: dict[str, float] = {}
        self._more_hint = more_hint  # `(/more #n)` on summaries; attach has no /more (Ruling R18)
        self._burst: Burst | None = None  # the burst still running
        self._bursts: dict[int, list[dict]] = {}  # closed bursts by number, for /more #n

    def _line(self, record: dict, width: int, summary: str | None = None, style: str = "phil.muted") -> Text:
        …  # unchanged, except: `line = Text(f"{head}{rest}", style=style)`

    def tool_lines(self, records: list[dict], width: int, cap: int | None = BURST_LINES) -> list[Text]:
        groups = fold([r for r in records if r.get("phase") == "end"])
        shown, extra = (groups, []) if cap is None else (groups[:cap], groups[cap:])
        lines: list[Text] = []
        for group in shown:
            summary = group_summary(group) if len(group) > 1 else None
            lines.append(self._line(group[-1], width, summary=summary))
        if extra:
            count = sum(len(g) for g in extra)
            noun = "reads" if all(r.get("tool") in LOOKS for g in extra for r in g) else "steps"
            lines.append(Text(f"{INDENT}… {count} more {noun}", style="phil.muted"))
        return lines

    def activity(self, records: list[dict], width: int) -> list[FeedItem]:
        """Feed lines for a batch of activity records (spec 2026-10-16 §5). A record of another
        (task, role) ends the open burst first (its summary line). Within a burst only failures
        print, at once; the engine's gate runs keep their own line each (Ruling R12)."""
        out: list[FeedItem] = []
        for record in records:
            key = key_of(record)
            if self._burst is not None and self._burst.key != key:
                out += self.end_burst(width)
            if record.get("phase") != "end":
                continue
            if key[1] == ENGINE:
                out.append(FeedItem(self._line(record, width), (record,)))
                continue
            if self._burst is None:
                self._burst = Burst(*key)
            self._burst.calls.append(record)
            if is_failure(record):
                out.append(FeedItem(self._line(record, width, style="phil.error"), (record,)))
        return out

    def end_burst(self, width: int) -> list[FeedItem]:
        """The open burst ended: its one summary line (none if nothing is open). It's kept for /more."""
        burst, self._burst = self._burst, None
        if burst is None or not burst.calls:
            return []
        number = burst.number
        if number is not None:
            self._bursts[number] = list(burst.calls)
        ref = f"  (/more #{number})" if self._more_hint and number is not None else ""
        body = _cut(f"{INDENT}{burst_text(burst)}", max(width - 1 - cell_len(ref), 0))
        return [FeedItem(Text(body + ref, style="phil.muted"), tuple(burst.calls))]

    def rolling(self) -> tuple[str, ...]:
        return rolling_lines(self._burst)

    def burst(self, number: int) -> list[dict] | None:
        calls = self._bursts.get(number)
        return list(calls) if calls is not None else None
```

(`_cut` already returns the text unchanged when it fits.)

- [ ] **Step 5: Implement the live area**

- **`src/phil/chat/state.py`:**
  - Add `rolling: tuple[str, ...] = ()  # the running burst's calls, under the main live row` to `ToolbarView`.
  - Add `def set_rolling(self, lines: tuple[str, ...]) -> None: self._update(rolling=lines)` to `ChatState`.
- **`src/phil/ui/toolbar.py`:** set `MAX_LIVE_LINES = 6`. In `render_live_rows`:
  - After the main row is appended, and before the sub-agent loop, add:

```python
        for line in view.rolling:
            rows.append([_Part("class:phil.muted", line, 0)])
```

  - Record `rolling = len(view.rolling) if view.run is not None else 0` before `if not rows:`.
  - Replace the collapse block with:

```python
    if max_lines == 1:
        extra = len(rows) - 1 - rolling  # other agents' rows only (Ruling R16)
        rows = [[*rows[0], _Part("class:phil.muted", f" +{extra} more")] if extra > 0 else rows[0]]
    elif len(rows) > MAX_LIVE_LINES:
        keep = MAX_LIVE_LINES - 1  # six lines in all, the `+N more` line included
        rows = [*rows[:keep], [_Part("class:phil.muted", f"  +{len(rows) - keep} more", 0)]]
```

  - Update the docstring: "at most `MAX_LIVE_LINES` lines in all, the `+N more` line included; the main agent's rolling lines come before sub-agent rows".

- [ ] **Step 6: Run the tests and see them pass**

Run: `uv run pytest tests/ui tests/chat/test_controller_feed.py tests/cli/test_attach.py -q -n 0`
Expected: PASS. The chat and attach still call `tool_lines`, so their tests are unchanged.

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`. Expected: PASS.

```
UI: tool-call bursts, a rolling view and one summary line

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/ui/bursts.py src/phil/ui/feed_view.py src/phil/ui/toolbar.py src/phil/chat/state.py tests/ui/test_bursts.py tests/ui/test_feed_view.py tests/ui/test_toolbar.py
git commit -F .git/phil-commit-msg.txt
```

---

### Task 6: Bursts in the chat, `/more #n`, `/feed` and `phil attach`

**Files:**
- Modify:
  - `src/phil/chat/controller.py` (`_on_activity`, `_on_milestone`, `_on_run_paused`, `_on_run_done`, `_clear_run`, `_more_step`; new `_print_feed`, `_end_burst`)
  - `src/phil/cli/attach.py`
- Test:
  - `tests/chat/test_controller_feed.py`
  - `tests/chat/test_controller_agents.py`
  - `tests/cli/test_attach.py`

**Interfaces:**
- **Consumes:** `FeedRenderer.activity/end_burst/rolling/burst`, `FeedItem`, `ChatState.set_rolling` (Task 5).
- **Produces:** the controller's `_print_feed(items: list[FeedItem]) -> None` and `_end_burst() -> None`.

- [ ] **Step 1: Write and update the tests** (`tests/chat/test_controller_feed.py`)

Add a helper beside `_activity`:

```python
def _flush(controller) -> str:
    """A milestone ends the open burst; returns what printed since the last export."""
    controller._handle(ChatEvent("milestone", {"kind": "task_done", "task": "CALC-001", "seq": 99}))
    return controller.console.export_text()
```

Replace `test_activity_and_milestones_print_feed_lines` with:

```python
def test_calls_roll_live_and_a_milestone_prints_the_bursts_summary_first(controller_with_run):
    controller = controller_with_run
    controller._handle(ChatEvent("milestone", {
        "kind": "task_started", "task": "CALC-001", "title": "Add multiply", "ts": "2026-10-07T10:00:00Z", "seq": 2,
    }))
    controller._handle(ChatEvent("activity", {"records": [{
        "seq": 3, "ts": "2026-10-07T10:00:05Z", "phase": "end", "task": "CALC-001", "role": "implementer",
        "tool": "run_shell", "summary": "run pytest -q", "duration_ms": 1200, "result": "→ 7 passed", "ok": True,
        "detail": "3.txt",
    }]}))
    text = controller.console.export_text()
    assert "▸ CALC-001 Add multiply" in text and "pytest -q" not in text
    assert controller.state.view().rolling == ("    run    pytest -q  → 7 passed · 1.2s",)
    controller._handle(ChatEvent("milestone", {"kind": "gate", "task": "CALC-001", "name": "green", "seq": 3}))
    text = controller.console.export_text()
    assert text.index("implementer ran 1 tool · ran pytest -q  (/more #3)") < text.index("✓ CALC-001 green")
    assert controller.state.view().rolling == ()


def test_a_failed_call_prints_at_once(controller_with_run):
    text = _activity(controller_with_run, {**_end(3, "implementer", "pytest -q"), "ok": False, "result": "→ 1 failed"})
    assert "run   pytest -q  → 1 failed" in text


def test_the_run_end_prints_an_open_bursts_summary_once_before_its_notice(controller_with_run):
    controller = controller_with_run
    _activity(controller, _end(3, "implementer", "make"))
    controller._handle(ChatEvent("run_done", {"state": "failed", "needs_attention": "boom"}))
    text = controller.console.export_text()
    assert text.index("implementer ran 1 tool · ran make") < text.index("failed: boom")
    assert text.count("implementer ran 1 tool") == 1
    assert controller.state.view().rolling == ()


def test_a_pause_ends_the_burst_before_its_notice(controller_with_run):
    controller = controller_with_run
    _activity(controller, _end(3, "implementer", "make"))
    controller._handle(ChatEvent("run_paused", {"escalation": {"summary": "CALC-001 failed", "options": ["abort"]}}))
    text = controller.console.export_text()
    assert text.index("implementer ran 1 tool · ran make") < text.index("needs you: CALC-001 failed")


def test_more_hash_prints_a_bursts_calls_then_its_last_steps_detail(controller_with_run):
    controller = controller_with_run
    run_dir = ProjectPaths(controller.info.slug).run_dir(controller._run_id)
    ActivityLog(run_dir).end(4, task="CALC-001", role="implementer", tool="run_shell", summary="run make",
                             result="→ ok", ok=True, detail="made it\n", duration_ms=10)
    _activity(controller, _end(3, "implementer", "npm ci"), _end(4, "implementer", "make", detail="4.txt"))
    _flush(controller)
    controller._more_command("#4")
    text = controller.console.export_text()
    assert text.index("run   npm ci  → ok") < text.index("run   make  → ok") < text.index("made it")
    assert "#4 has no details." not in text
```

Update the existing tests:
- **`test_forgetting_the_run_clears_the_live_step_and_the_feed`:** add `assert controller.state.view().rolling == ()` after the `_forget_run()` call.
- **`test_feed_filter_shows_only_that_agent`:** replace its middle section, from the `_activity(...)` call to before `controller._command("/feed")`, with:

```python
    _activity(controller, _end(3, "implementer", "impl-cmd"), _end(4, "tester", "tester-cmd"))
    controller._handle(ChatEvent("milestone", {
        "kind": "task_started", "task": "CALC-001", "title": "Add multiply", "ts": "2026-10-07T10:00:00Z", "seq": 5,
    }))
    text = controller.console.export_text()
    assert "tester ran 1 tool · ran tester-cmd" in text
    assert "impl-cmd" not in text
    assert "▸ CALC-001 Add multiply" in text
```

- **`test_feed_counts_several_hidden_steps`:**

```python
    text = _activity(controller, _end(3, "implementer", "a"), _end(4, "tester", "b"), _end(5, "reviewer", "c"))
    text += _flush(controller)  # summaries print both as a batch closes a burst and at the milestone
    assert "reviewer ran 1 tool · ran c" in text and "ran a" not in text and "ran b" not in text
```

- **`test_feed_sub_agent_and_engine`:** run each filter on a fresh renderer and flush. Replace each `text = _activity(controller, *records)` with `controller._feed = FeedRenderer(); text = _activity(controller, *records) + _flush(controller)`, importing `FeedRenderer` from `phil.ui.feed_view`. A summary prints during `_activity` when a later record closes its burst, so both exports count. Then use these assertions:
  - **`/feed sub-agent`:** `"implementer ran 2 tools · ran sub-cmd" in text`, and none of `own-cmd`, `gate-cmd` or `tester-cmd` is in the text.
  - **`/feed engine`:** `"gate-cmd" in text`, and none of `implementer ran`, `sub-cmd` or `tester-cmd` is in the text.
  - **`/feed implementer`:** `"implementer ran 2 tools" in text`, with `"gate-cmd" not in text and "tester-cmd" not in text`.
- **`test_feed_filter_applies_before_folding_and_keeps_start_records`:** rename it to `test_feed_hides_other_agents_summaries_and_counts_only_end_records` with this body:

```python
    controller = controller_with_run
    controller._command("/feed tester")
    controller.console.export_text()
    read = {"phase": "end", "task": "CALC-001", "tool": "read_file", "ok": True}
    text = _activity(
        controller,
        {**read, "seq": 3, "role": "tester", "summary": "read a.py"},
        {**read, "seq": 4, "role": "implementer", "summary": "read b.py"},
        {**read, "seq": 5, "role": "tester", "summary": "read c.py"},
        {"seq": 6, "phase": "start", "task": "CALC-001", "role": "implementer", "tool": "run_shell", "summary": "x"},
    )
    assert text.count("tester ran 1 tool") == 2
    assert "implementer ran" not in text
    controller._command("/feed")
    assert "1 step from other agents was hidden." in controller.console.export_text()  # start records aren't steps
```

- **`test_the_filter_resets_when_the_run_ends`:** change its last two lines to:

```python
    text = _activity(controller, _end(3, "implementer", "impl-cmd"), _end(4, "tester", "tester-cmd"))
    text += _flush(controller)
    assert "ran impl-cmd" in text and "ran tester-cmd" in text
```

- **`test_a_scripted_run_shows_its_feed_in_the_chat`:** replace `assert "run   pytest -q  → 7 passed" in text` with `assert "implementer ran " in text and "(/more #" in text`.

`tests/chat/test_controller_agents.py`:
- Change `*_, runs, _, _ = run_chat(` to `text, _, runs, _, _ = run_chat(`.
- Replace the assertion `assert "read  test_calc.py" in printed` with:

```python
    feed = printed + text
    assert "read  test_calc.py" not in feed  # a sub-agent's call is in its parent's burst, not its own line
    assert "implementer ran " in feed
```

`tests/cli/test_attach.py`:
- **The first test:** rename `test_attach_prints_milestones_and_new_tool_lines` to `test_attach_prints_milestones_and_burst_summaries`. Replace its `assert "run   pytest -q  → 7 passed" in text` with:

```python
    assert "implementer ran 1 tool · ran pytest -q" in text
    assert "(/more" not in text  # attach has no /more (Ruling R18)
```

- **Add:**

```python
def test_attach_prints_failures_at_once_and_a_summary_when_the_run_fails(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    update_run(conn, record.run_id, state="running")
    events = run_events(paths, record.run_id)
    activity = ActivityLog(paths.run_dir(record.run_id))

    def sleep(_):
        activity.end(1, task="CALC-001", role="implementer", tool="edit_file", summary="edit calc.py",
                     result="+1 −0", ok=True, detail=None, duration_ms=5)
        activity.end(2, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
                     result="→ 1 failed", ok=False, detail="1 failed\n", duration_ms=5)
        update_run(conn, record.run_id, state="failed")

    io = AttachIO(choose=lambda p, o: "abort", ask_hint=lambda: None, spawn=lambda m, d: None, sleep=sleep,
                  alive=lambda r: False, starting=lambda e: False)
    console = make_console(record=True, width=120)
    assert attach(conn, record.run_id, events, console, io, poll_s=0) == "failed"
    text = console.export_text()
    failure, summary = text.index("run   pytest -q  → 1 failed"), text.index("implementer ran 2 tools · edited calc.py")
    assert failure < summary < text.index("Continue with `phil resume")
    assert text.count("implementer ran 2 tools") == 1
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/chat/test_controller_feed.py tests/chat/test_controller_agents.py tests/cli/test_attach.py -q -n 0`
Expected: FAIL. Tool lines still print one by one, and `state.view().rolling` stays `()`.

- [ ] **Step 3: Implement the chat** (`src/phil/chat/controller.py`)

Import `from phil.ui.bursts import FeedItem`. Replace `_on_activity` and `_on_milestone`, and add two helpers beside them:

```python
    def _print_feed(self, items: list[FeedItem]) -> None:
        """Print feed items the /feed filter shows: a summary shows when any of its calls would (R19)."""
        agent = self._feed_filter
        for item in items:
            if agent is None or any(_shows(record, agent) for record in item.records):
                self.console.print(item.line, soft_wrap=True)

    def _end_burst(self) -> None:
        """The open burst ended (a milestone, a pause, the run's end): its summary, and no rolling view."""
        self._print_feed(self._feed.end_burst(self.console.width))
        self.state.set_rolling(())

    def _on_activity(self, data: dict) -> None:
        records = data.get("records", [])
        agent = self._feed_filter
        if agent is not None:  # steps (end records) from other agents; start records aren't steps
            self._hidden += sum(1 for r in records if r.get("phase") == "end" and not _shows(r, agent))
        self._print_feed(self._feed.activity(records, self.console.width))
        self.state.set_rolling(self._feed.rolling())

    def _on_milestone(self, data: dict) -> None:
        self._end_burst()
        self.console.print(self._feed.milestone(data, self.console.width))
```

Then:
- **`_on_run_paused`:** call `self._end_burst()` as its first line (Ruling R11).
- **`_on_run_done`:** call `self._end_burst()` right after `self._stop_watcher()` and before `self._clear_run()`. The filter is still set then, and the summary comes before the notice (spec §7).
- **`_clear_run`:** add `self.state.set_rolling(())`.
- **`_more_step`:** replace it from `log = activity_log(...)` on:

```python
        log = activity_log(ProjectPaths(self.info.slug), run_id)
        calls = self._feed.burst(seq)  # a burst's number first (Ruling R15)
        if calls is not None:
            for line in self._feed.tool_lines(calls, self.console.width, cap=None):
                self.console.print(line, soft_wrap=True)
        path = log.detail_for(seq)
        if path is None:
            if calls is None:
                self.console.print(f"#{seq} has no details.")
            return
        try:
            text = detail_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            self.console.print(f"[phil.error]Couldn't read {escape(str(path))}: {escape(type(exc).__name__)}[/]")
            return
        record = log.find(seq) or {}
        self.console.print(Panel(Text(text), title=Text(str(record.get("summary", f"#{seq}"))), title_align="left"))
```

Update its docstring: "`/more #n`: a burst's calls when n numbers one, then step n's detail in a panel …".

- [ ] **Step 4: Implement attach** (`src/phil/cli/attach.py`)

```python
def _print_items(console: Console, items) -> None:
    for item in items:
        console.print(item.line, soft_wrap=True)
```

In `attach`:
- Set `feed = FeedRenderer(more_hint=False)`.
- Make the loop body over `interleave(new, records)`:

```python
            if item == "event":
                if value["kind"] in MILESTONE_KINDS or value["kind"] == "escalation":
                    _print_items(console, feed.end_burst(console.width))
                render_event(console, value, run_id, feed)
            else:
                _print_items(console, feed.activity(value, console.width))
```

- Insert `_print_items(console, feed.end_burst(console.width))` as the first line of the `if record.state in TERMINAL:` block, and again as the first line of the `if record.state in ("failed", "stopped"):` block.

- [ ] **Step 5: Run the tests and see them pass**

Run: `uv run pytest tests/chat/test_controller_feed.py tests/chat/test_controller_agents.py tests/cli/test_attach.py tests/chat -q -n 0`
Expected: PASS.

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`. Expected: PASS.

```
Chat and attach: one summary line per tool burst; /more #n lists it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/chat/controller.py src/phil/cli/attach.py tests/chat/test_controller_feed.py tests/chat/test_controller_agents.py tests/cli/test_attach.py
git commit -F .git/phil-commit-msg.txt
```

---

### Task 7: Callouts ask once and leave a record line

**Files:**
- Modify: `src/phil/chat/controller.py` (`_ask_next`, `_build_decision`, `_answers`, `_loop`; new `_record_answer`)
- Test: `tests/chat/test_controller_callouts.py`

**Interfaces:**
- **Produces:** `ChatController._record_answer(question: str, answer: str) -> None`.

- [ ] **Step 1: Write and update the tests** (`tests/chat/test_controller_callouts.py`)

Add `from rich.cells import cell_len`.

Replace `test_with_a_menu_the_printed_option_lists_are_left_to_the_callout` with:

```python
def test_with_a_menu_a_question_shows_only_in_its_callout(controller_with_choose):
    """With `choose`, the callout alone asks (no printed copy; without it the question still prints:
    test_questions_flow); the answer leaves one record line instead of an "Answer:" line."""
    question = Question(text="Which module?", options=["calc.py", "ops.py"], why="It decides the file.")
    scripts = {"intake": [goal(open_questions=[question]), goal()], "architect": [plan()], "critic": [critique()]}
    text, *_ = controller_with_choose(["add subtract", "1", "n"], scripts)
    assert "1. Which module?" not in text and "It decides the file." not in text
    assert "1. calc.py" not in text and "Something else" not in text
    assert "? Which module? → calc.py" in text
    assert "Answer: calc.py" not in text
    assert "· Cancel" in text  # other decisions keep their settled lines


def test_typed_text_is_recorded_as_typed(controller_with_choose):
    question = Question(text="Which module?", options=["calc.py", "ops.py"])
    scripts = {"intake": [goal(open_questions=[question]), goal()], "architect": [plan()], "critic": [critique()]}
    text, *_ = controller_with_choose(["add subtract", "3", "lib/ops_extra.py", "n"], scripts)
    assert "? Which module? → lib/ops_extra.py" in text


def test_a_long_record_line_is_cut_to_the_width(controller_with_choose):
    question = Question(text="Which module?", options=["calc.py", "ops.py"])
    scripts = {"intake": [goal(open_questions=[question]), goal()], "architect": [plan()], "critic": [critique()]}
    long = "put it in " + "a very long place " * 12
    text, *_ = controller_with_choose(["add subtract", "3", long, "n"], scripts)
    [line] = [line for line in text.splitlines() if line.startswith("? Which module?")]
    assert line.endswith("…") and cell_len(line) <= 119


def test_plan_with_what_you_know_keeps_its_settled_line(controller_with_choose):
    question = Question(text="Which module?", options=["calc.py", "ops.py"])
    scripts = {"intake": [goal(open_questions=[question])], "architect": [plan()], "critic": [critique()]}
    text, *_ = controller_with_choose(["add subtract", "go", "n"], scripts)
    assert "Answer: Plan with what you know" in text and "? Which module?" not in text


def test_a_free_text_question_has_no_callout(calc_repo):
    controller = bare_controller(calc_repo)
    controller.io.choose = lambda prompt, decision: None
    controller._pending, controller._replies = [Question(text="Anything else to know?")], []
    controller._set_stage("questions")
    assert controller._build_decision() is None  # today: AttributeError from _with_details(None)
```

Change `test_each_answer_follows_its_question`'s ordering line to:

```python
    order = [text.index(s) for s in ("? Which module? → calc.py", "? Which name? → minus")]
```

Update its docstring to: "Two questions answered through choose: each leaves its record line, in order."

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/chat/test_controller_callouts.py -q -n 0`
Expected: FAIL. The question still prints, `Answer: calc.py` still prints, and `_build_decision` raises `AttributeError`.

- [ ] **Step 3: Implement** (`src/phil/chat/controller.py`)

`_ask_next` becomes:

```python
    def _ask_next(self) -> None:
        """Print the next question when no callout asks it: its number, text, why, and numbered
        options plus "Something else". With a menu, a question with options is asked by its callout
        alone (spec 2026-10-16 §6.1); a free-text question has no callout, so it still prints (R20)."""
        question = self._pending[len(self._replies)]
        if self.io.choose is not None and question.options:
            return
        self.console.print(f"[phil.agent]{len(self._replies) + 1}. {escape(_clip(question.text))}[/]")
        if question.why:
            self.console.print(f"   [phil.muted]{escape(_clip(question.why))}[/]")
        if self.io.choose is not None:
            return
        for n, option in enumerate(question.options, 1):
            self.console.print(f"     {n}. {escape(_clip(option))}")
        if question.options:
            self.console.print(f"     {len(question.options) + 1}. {SOMETHING_ELSE}")
```

In `_build_decision`, the questions branch becomes:

```python
            decision = question_decision(index + 1, len(self._pending), question.text, question.why, question.options)
            if decision is None:  # a free-text question: typed at the prompt, no callout (R20)
                return None
            return self._with_details(decision, [question.why])
```

In `_answers`, right after `if answer is None: return`, add:

```python
        if self.io.choose is not None and question.options:
            self._record_answer(question.text, answer)  # its callout is gone: one line says what was chosen
```

Add:

```python
    def _record_answer(self, question: str, answer: str) -> None:
        """`? question → answer`, muted, cut to the width with … (spec 2026-10-16 §6.1; R22)."""
        line = printable(f"? {question} → {answer}")
        self.console.print(Text(_fit(line, self.console.width), style="phil.muted"))
```

In `_loop`, change the settled-line condition:

```python
                        option = next((o for o in decision.options if o.answer == raw), None)
                        # A clarifying question's answer leaves its record line instead (_answers; R21).
                        recorded = option is not None and self.stage == "questions" and option.answer != "go"
                        if option is not None and not option.typed and not recorded:
```

(Leave the body under that `if` unchanged.)

- [ ] **Step 4: Add the end-to-end check, then run the tests**

This check is added only now. Before Step 3 it would spin forever, and the suite has no per-test timeout.

```python
def test_a_free_text_question_with_a_menu_prints_once(controller_with_choose):
    question = Question(text="Anything else to know?")
    scripts = {"intake": [goal(open_questions=[question]), goal()], "architect": [plan()], "critic": [critique()]}
    text, *_ = controller_with_choose(["add subtract", "nothing else", "n"], scripts)
    assert text.count("Anything else to know?") == 1
    assert "→ nothing else" not in text
```

Run: `uv run pytest tests/chat/test_controller_callouts.py tests/chat/test_questions_flow.py tests/chat/test_decision.py tests/chat/test_design_flow.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Expected: PASS.

```
Chat: a question shows once, in its callout; a record line after

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/chat/controller.py tests/chat/test_controller_callouts.py
git commit -F .git/phil-commit-msg.txt
```

---

### Task 8: Start-up — the open-chats list, no tips line, no parked count

**Files:**
- Modify:
  - `src/phil/ui/banner.py`
  - `src/phil/cli/main.py` (`_open_chat_line`, `_choose_open_chat`, `_banner_facts`, `_chat`)
  - `src/phil/chat/session.py` (`ChatSummary`, `list_open_chats`)
- Test:
  - `tests/ui/test_banner.py`
  - `tests/cli/test_banner_startup.py`
  - `tests/cli/test_chat_command.py`

**Interfaces:**
- **Consumes:**
  - `label_for` and `run_label` (Task 3)
  - `RunRecord.name` (Task 2)
  - `create_run(..., name=)`
- **Produces:**
  - `BannerFacts` loses `parked` and gains `run_label: str = ""` (last field)
  - `banner.TIPS`, `TIPS_SEGMENTS` and `TIPS_DROP` are removed; `FIRST_TIPS` and `FIRST_TIPS_SEGMENTS` stay
  - `ChatSummary.run_name: str | None = None` (last field)
  - `_banner_facts(..., session, listed=None)`
  - `_open_chat_line(n, chat, width) -> Text`

- [ ] **Step 1: Write and update the tests**

`tests/ui/test_banner.py`:
- **Imports:** `from phil.ui.banner import FIRST_TIPS, GAP, NARROW_BOX, NARROW_MASCOT, BannerFacts, banner_plain, render_banner`.
- **`FACTS`:** delete `parked=3,`.
- **`test_facts_content`:** the assertion becomes `assert "calc @ main a1b2c3d · ⚠ 2 uncommitted" in text and "parked" not in text`.
- **`test_plain_says_uncommitted_files_stay_out_of_runs`:** the expected line becomes `"calc @ main a1b2c3d · ⚠ 2 uncommitted (not included in runs)"`.
- **Delete:** `test_tips_drop_whole_segments_at_80_columns` and `test_tips_drop_feed_before_btw_then_cut`.
- **Replace:** `test_tips_first_time_and_returning` and `test_tips_name_commands_in_normal_weight` with:

```python
def test_no_tips_line_and_the_first_start_keeps_its_welcome():
    returning = "\n".join(t.plain for t in render_banner(FACTS, 140))
    assert "Type a goal to start" not in returning and "/feed" not in returning
    first = BannerFacts(**{**FACTS.__dict__, "first_time": True})
    assert any(t.plain.startswith("Describe what you want built.") for t in render_banner(first, 140))


def test_the_welcome_names_help_in_normal_weight():
    first = BannerFacts(**{**FACTS.__dict__, "first_time": True})
    line = next(t for t in render_banner(first, 140) if t.plain == FIRST_TIPS)
    assert _styles_at(line, line.plain.index("/help")) == set()
    assert _styles_at(line, 0) == {"phil.muted"}


def test_the_run_line_uses_the_label_and_keeps_the_id_in_the_hint():
    facts = BannerFacts(**{**FACTS.__dict__, "run": ("r-1", "failed", None), "run_label": "newsletter-cta (r-1)"})
    assert "newsletter-cta (r-1) failed · phil resume r-1" in [t.plain for t in render_banner(facts, 140)]
```

`tests/cli/test_banner_startup.py`:
- **Imports:** `from phil.ui.banner import FIRST_TIPS` and `from phil.store.runs import create_run, update_run`.
- **Tips assertions:** replace every `assert TIPS in output` / `assert TIPS in result.output` with `assert "Type a goal to start" not in output` (or `result.output`). This affects `test_chat_opens_with_the_card_and_no_help_paragraph`, `test_piped_start_prints_plain_facts`, `test_a_failing_render_falls_back_to_plain_lines` and `test_a_saved_chat_with_no_runs_is_not_a_first_visit`.
- **`test_first_time_tips`:** delete `assert TIPS not in result.output`.
- **Add:**

```python
def test_the_open_chats_list_is_the_only_mention_of_its_runs(calc_repo, monkeypatch):
    paths = _add_run(calc_repo, "r-9001", "running", "failed")
    _open_chat(paths, 1)  # its run is r-9001
    _tty(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="\n", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    output = _plain(result.output)
    assert "Open chats:" in output
    assert "1. r-9001 · run failed · (no goal)" in output  # an old, unnamed run shows by its ID
    assert "phil resume r-9001" not in output  # no separate run line
    assert "other open chat" not in output


def test_a_run_outside_the_listed_chats_keeps_its_labelled_run_line(calc_repo, monkeypatch):
    paths = _add_run(calc_repo, "r-9001", "running", "completed")
    _open_chat(paths, 1)
    conn = connect(paths.db_path)
    create_run(conn, run_id="r-0007", keyword="calc", base_sha="0" * 40, worktree=paths.project_dir / "wt",
               tasks_total=1, name="nightly-docs")  # e.g. started with `phil run`: no chat holds it
    update_run(conn, "r-0007", state="running")
    update_run(conn, "r-0007", state="failed")
    _tty(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="\n", env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    assert "nightly-docs (r-0007) failed · phil resume r-0007" in _plain(result.output)


def test_the_banner_has_no_parked_count(calc_repo):
    _add_run(calc_repo, "r-0001", "running", "completed")
    from phil.store.parked import park
    from phil.contracts import Ref
    park(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path), raised_by="user", note="later",
         why_not_now="test", source=Ref(label="t", path="t"), run_id=None)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert " · 1 parked" not in result.output  # the banner's old form; the status bar keeps its own count
```

`tests/cli/test_chat_command.py` (add `from phil.store.db import connect` and `from phil.store.runs import list_runs` if missing):
- **`test_tty_lists_open_chats_and_reopens_one`:** replace `assert f"1. {chat_id} · " in output` and `assert " · run r-" in output` with:

```python
    [run] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    assert f"1. {run.name} · run " in output  # label · run state · goal; no chat id
```

- **`test_the_open_chats_list_marks_chats_open_elsewhere`:** replace `assert f"1. {chat_id} · " in output and "open elsewhere" in output` with:

```python
    [run] = list_runs(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path))
    assert f"1. {run.name} · run " in output and "open elsewhere" in output
```

- **`test_tty_enter_starts_a_new_chat_and_new_skips_the_list`:** `assert f"1. {chat_id}" not in fresh_output` still holds; leave it.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/ui/test_banner.py tests/cli/test_banner_startup.py tests/cli/test_chat_command.py -q -n 0`
Expected: FAIL. The import of `TIPS` is gone from the tests, but `BannerFacts` still needs `parked`; the run line is still printed with the picker; rows still start with the chat ID.

- [ ] **Step 3: Implement the banner** (`src/phil/ui/banner.py`)

- **Delete:** `TIPS_SEGMENTS`, `TIPS_DROP` and `TIPS`, and their comment.
- **Docstring:** "… with "pick up where you left off" below, and a welcome sentence on a first start".
- **`BannerFacts`:** delete `parked: int`; add `run_label: str = ""  # the run line's label (R24); "": the ID` as its last field.
- **`_facts`:** delete the `if f.parked:` block.
- **`_below`:** becomes:

```python
def _below(f: BannerFacts) -> list[_Line]:
    lines: list[_Line] = []
    if f.run:
        run_id, state, summary = f.run
        label = f.run_label or run_id
        # The banner prints before the chat is chosen, so each hint is a command that works from any chat.
        if state == "escalated":
            shown = _summary(summary)
            why = f" ({shown})" if shown else ""
            lines.append(_one(Text(f"⏸ {label} is waiting for you{why} · phil attach {run_id}", "phil.warn")))
        elif state == "failed":
            lines.append(_one(Text(f"{label} failed · phil resume {run_id}")))
        elif state == "stopped":
            lines.append(_one(Text(f"{label} was stopped · phil resume {run_id}")))
        elif state in ("running", "pending"):
            lines.append(_one(Text(f"{label} is running · phil attach {run_id}")))
    if f.other_chats:
        n = len(f.other_chats)
        lines.append(_one(Text(f"{n} other open chat{'s' if n != 1 else ''} · phil --resume {f.other_chats[0]}", MUTED)))
    if f.first_time:  # the idle status bar already says what the tips line said (spec 2026-10-16 §6.2)
        lines.append(_Line(_segments(FIRST_TIPS_SEGMENTS)))
    return lines
```

- [ ] **Step 4: Implement the start-up** (`src/phil/chat/session.py`, `src/phil/cli/main.py`)

`session.py`:
- Add `run_name: str | None = None` as `ChatSummary`'s last field.
- In `list_open_chats`, pass `run_name=record.name if record else None` to `ChatSummary(...)`.

`main.py`:

```python
def _open_chat_line(n: int, chat, width: int) -> Text:
    """`n. run label · run state · goal`, cut to the width with … (spec 2026-10-16 §6.2). A chat with
    no run shows only its goal; `open elsewhere` sits before the goal so a cut can't hide it (R23)."""
    from phil.ui.runs import label_for

    line = Text(f"{n}. ")
    if chat.run_id:
        line.append(label_for(chat.run_id, chat.run_name, first=False), "phil.id")
        line.append(f" · run {chat.run_state or 'unknown'} · ")
    if chat.open_elsewhere:
        line.append("open elsewhere", "phil.warn")
        line.append(" · ")
    if chat.objective:
        line.append(printable(chat.objective))
    else:
        line.append("(no goal)", "phil.muted")
    line.truncate(max(width - 1, 1), overflow="ellipsis")
    return line
```

- **`_choose_open_chat`:** the row print becomes `out.print(_open_chat_line(n, chat, out.width), soft_wrap=True)`.
- **`_banner_facts`:** add the parameter `listed=None` after `session`, and extend the docstring: "`listed`: the open chats the picker is about to list (None: no picker). They are the only mention of their runs, so the run line is left out for a run one of them holds, and the other-chats line is left out altogether (spec 2026-10-16 §6.2)."
  - **Remove** the `parked` lookup and `parked=parked` from `BannerFacts(...)`. `list_parked` stays imported, because `phil parked` uses it.
  - **The pick-up block:** after `record = next(...)`, add:

```python
    listed_runs = {chat.run_id for chat in listed or () if chat.run_id}
    if record is not None and record.run_id in listed_runs:
        record = None  # the open-chats list names it
```

  - **The label:** after `run = (record.run_id, state, summary)`, add:

```python
        try:
            label = run_label(record, first=True)
        except Exception:
            label = record.run_id
```

    Initialise `label = ""` before the `if record is not None:` block, and pass `run_label=label` to `BannerFacts(...)`. Import `from phil.ui.runs import run_label` inside the function, beside the other local imports.
  - **The other-chats lookup:** prefix it with `if listed is not None: other_chats = () else:` (the `try` moves under `else:`).
- **`_chat`:** compute the list before the banner and reuse it for the picker:

```python
    listed = list_open_chats(paths, conn) if session is None and not new and tty else []
    facts = _banner_facts(info, config, conn, paths, base_label, header_sha, base, session, listed=listed or None)
```

  Then replace the picker block with:

```python
    if listed:
        chosen = _choose_open_chat(out, listed, lambda prompt: out.input(f"[phil.user]{escape(prompt)}[/]"))
        if chosen is not None:
            session = _reopen_chat(ChatSession, paths, chosen)
```

- [ ] **Step 5: Run the tests and see them pass**

Run: `uv run pytest tests/ui/test_banner.py tests/cli -q -n 0`
Expected: PASS.

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`. Expected: PASS.

```
Start-up: one list of open chats; no tips line or parked count

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

```bash
git add src/phil/ui/banner.py src/phil/cli/main.py src/phil/chat/session.py tests/ui/test_banner.py tests/cli/test_banner_startup.py tests/cli/test_chat_command.py
git commit -F .git/phil-commit-msg.txt
```

---

## Self-review notes

- **Spec coverage:**

| Spec section | Task |
|---|---|
| §3.1 Where the name comes from | 1 |
| §3.2 Storage | 2 |
| §3.3 Display | 3 (chat, show, runs), 4 (status bar), 8 (open-chats list, banner) |
| §4.1 Status bar | 4 |
| §4.2 Live rows | 4 (agent only), 5 (rolling, cap) |
| §5.1–§5.3 Bursts | 5 (model), 6 (chat) |
| §5.4 Failures | 5, 6 |
| §5.5 attach and /feed | 6 |
| §6.1 Questions once | 7 |
| §6.2 Start-up lines | 8 |
| §6.3 Parked | 8 |
| §7 Error handling | 1 (names), 3 (missing names), 6 (burst closed at run end) |
| §8 Testing | across the tasks; existing exact-text tests are updated in the task that changes the text |

- **Interface consistency:** names used across tasks are:
  - **Names and labels:** `run_name`, `built_name`, `normalise`, `RunRecord.name`, `create_run(name=)`, `prepare_run(goal=)`, `label_for`, `run_label`, `ChatState.mention`, `labelled_runs`, `_label`, `_short_label`, `_run_name`, `label_of`.
  - **Status bar:** `RunView.name`, `RunView.title`, `TITLE_FLOOR`.
  - **Bursts:** `Burst`, `FeedItem`, `fold`, `group_summary`, `call_text`, `rolling_lines`, `burst_text`, `FeedRenderer.activity`, `end_burst`, `rolling`, `burst`, `more_hint`, `ToolbarView.rolling`, `set_rolling`, `_print_feed`, `_end_burst`.
  - **Callouts and start-up:** `_record_answer`, `BannerFacts.run_label`, `ChatSummary.run_name`, `_open_chat_line(n, chat, width)`.
- **Placeholder scan:** none. Task 3's mention edits and test pins are given as exact before/after fragments, because the edits are many one-line substitutions.
