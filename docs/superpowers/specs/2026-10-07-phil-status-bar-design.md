# Phil: Status Bar

**Status:** approved in conversation, 2026-10-07. This is the third slice of roadmap M5. It builds on the activity feed (PR #30) and the callouts (PR #31).

## 1. Problem

The chat's bottom toolbar is one dim line. It shows the step in progress, the run (`r-4f2a · calc 1/3 · implement · 1m 04s`), a pause notice, the number of `/btw` questions in flight, the chat's cost and the number of parked items. It doesn't show:

- which repo and branch this window is working in;
- which model is doing the work, and so what you're paying for;
- how the spend compares with the run's budget;
- how far through the plan the run is, beyond a bare `2/3`.

Since the activity feed landed, the live row above the input also shows the current step, so the toolbar repeats it.

## 2. Decisions (user, 2026-10-07)

| Topic | Decision |
|---|---|
| Purpose | All four: where am I (repo, branch, stage), spend against limits, the model in use, and run progress. |
| Layout | One dense line, made of segments separated by `│`. |
| Drop order on a narrow terminal (first listed drops first) | `/btw` and parked items → repo @ branch → model → tokens → elapsed → cost vs budget → run progress. A pause notice never drops. |
| Spinner | The braille dots, as today (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`). |
| Run progress | Task dots: `●` done, `◉` current (pulsing), `○` not started. |
| Roadmap | Visual proposals move from M5 to M6 (done in this branch). |

Mockups: `.superpowers/brainstorm/` (not committed), `status-layout.html` and `progress-styles.html`.

## 3. Design

### 3.1 The line in each state

| State | The line at full width |
|---|---|
| idle | `calc @ main │ Phil · type a goal, or /help │ chat $0.06` |
| working on a goal (intake, planning, design) | `calc @ main │ ⠹ Architect drafting · 12s │ high·claude-sonnet-5 │ chat $0.06` |
| run in progress | `calc @ phil/r-4f2a │ r-4f2a ●◉○ implement · 1m 04s │ low·gemini-3.8-flash │ 182k tok │ $0.41/$1.00` |
| run paused | `calc @ phil/r-4f2a │ ⏸ r-4f2a needs you │ r-4f2a ●◉○ │ 182k tok │ $0.41/$1.00` |

- `/btw ×2` and `3 parked` are appended when present.
- During a run, the step label isn't repeated, because the live row shows it.
- A goal step that's being cancelled keeps its `(cancelling…)` suffix.

### 3.2 Segments

- **Repo @ branch.**
  - The repo is the name of the chat's repo-root folder.
  - The branch is the repo's current branch. It's read with `git rev-parse --abbrev-ref HEAD` when the chat starts, and again whenever a run starts or ends.
  - While a run is active, the branch is the run's own branch, from its record.
  - The segment is dim, with the repo name in normal weight.
- **Model.**
  - It shows the tier and a short name, for example `low·gemini-3.8-flash`.
  - The short name drops the provider prefix (everything up to the first `:`) and any vendor path (everything up to the last `/`): `openrouter:google/gemini-3.8-flash` becomes `gemini-3.8-flash`, and `ollama:qwen3:8b` becomes `qwen3:8b`.
  - It's the model for the role that's working right now:
    - during a run, the live step's role;
    - while working on a goal, the step's role (intake: `classifier` if one is configured, otherwise `orchestrator`; `architect`, `revise`: `architect`; `critic`: `critic`; `designing`: `architect`);
    - otherwise the segment is omitted.
  - It's resolved with `config.model_for(role)`, and the tier comes from `config.model_owner(role)`, or whatever the config exposes for a role's tier.
- **Tokens.** The run's total, from the watcher's `run_progress` event, formatted as `182k tok`, `1.2M tok`, or `950 tok` below 1,000.
- **Cost against budget.**
  - During a run it shows `$<run cost>/$<limit>`. The limit is `config.run.max_cost_usd`, or the latest `budget_raised` limit if there is one.
  - Its colour is the normal style below 80% of the limit, `phil.warn` from 80%, and `phil.error` at 100% or more.
  - With no run, it shows `chat $<chat cost>` with no limit.
  - An estimated cost is marked `~`, and an unknown cost `?` (the existing cost-source marks).
  - With no limit configured (0 or unset), it shows the cost alone.
- **Run progress.**
  - It shows the run id, the task dots, the current stage label, and the elapsed time.
  - Done tasks are `●` in `phil.gate.pass`.
  - The current task is `◉`. It alternates `◉`/`○` every 0.5 s while the run is working, and is a steady `◉` in `phil.warn` while paused.
  - Tasks not started are `○`, dim.
  - When there are more than 12 tasks, it shows `●●●…◉ 14/20`: three done dots, an ellipsis, the current dot and the count.
- **Pause notice.** `⏸ <run> needs you`, in `phil.warn`. It never drops.

### 3.3 The budget limit after "Keep going"

When a budget pause is answered with `continue`, the engine raises the run's limits. It will also append `budget_raised {max_cost_usd, max_tokens}` (the new absolute limits) to the run's `events.jsonl`. The watcher posts the latest one as a chat event, and the status bar uses it from then on. A reopened chat's watcher reads the latest `budget_raised` from the log when it starts.

### 3.4 Rendering

- **Styled fragments.** `render_toolbar(view, now, width)` returns prompt_toolkit formatted-text fragments (`list[tuple[str, str]]`) instead of a plain string. Segment styles use the prompt_toolkit `Style` added with the callouts, extended with the `phil.*` styles the toolbar needs: `phil.muted`, `phil.warn`, `phil.error`, `phil.gate.pass`, `phil.id` and `phil.cost`.
- **Fitting to the width.** Whole segments are removed in the drop order until the line fits within `width - 1` cells, measured with `cell_len`. The final line is cut with `…` if even the essential segments don't fit.
- **No markup.** Agent and repo text (goal names, branch names) is plain text.
- **The state behind the line.** `ToolbarView` gains:
  - `repo`, `branch`;
  - `model`, as `(tier, short_name)`, or None;
  - `tokens`;
  - `budget_usd`, the limit;
  - `run_cost`.

  The controller keeps these up to date from events it already handles.

### 3.5 Testing

- **Unit tests** (`render_toolbar`):
  - each state in 3.1;
  - the drop order at widths 40, 60, 80 and 120;
  - the budget colours at 79%, 80% and 100%;
  - no limit;
  - estimated and unknown cost marks;
  - dots for 1, 3, 12 and 20 tasks;
  - the pulse alternating, and steady when paused;
  - short model names (the two examples above, plus a bare `model-x`);
  - the token formatting.
- **Engine:** `continue` writes `budget_raised` with the new limits.
- **Watcher:** it posts `budget_raised`, including one already in the log when it starts.
- **Controller:**
  - the repo, branch and model fields are set at chat start, during a goal step, during a run, and after a run ends;
  - the branch is re-read when a run starts and when it ends.
- **End to end:** a scripted run, followed by the toolbar's plain text at width 120, which contains the repo, the dots, the model and the cost against the budget.

## 4. Out of scope

- The welcome banner and the sub-agent bar.
- Full screen versus inline.
- Configuring which segments are shown.
- Showing the high- and low-tier models at the same time.

## 5. Done means

- The chat's status line shows the repo and branch, the model in use, the run's progress as task dots, tokens, and cost against the budget, and drops segments in the agreed order.
- The budget meter stays correct after "Keep going".
- CI passes on Ubuntu, macOS and Windows.
- The user checks it live.
