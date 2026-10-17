# Phil: UI Polish (run names, one place per fact, tool summaries)

**Status:** approved in conversation, 2026-10-16. This is release item 5, the polish pass, moved before packaging at the user's request. It revisits two earlier decisions: the dense status bar (`2026-10-07-phil-status-bar-design.md`) and the tool lines in the activity feed (`2026-10-07-phil-activity-feed-design.md`). The mascot stays a placeholder for now.

## 1. Problem

The chat screen "competes with a lot of information" (user, 2026-10-16). Looking at a real session:

- **Runs have only an ID.** `r-4f1c` appears everywhere, and nothing human-readable says which run it is.
- **The same fact shows in several places.**
  - The current task and step are in the status bar, the live rows above the prompt and the milestone bands.
  - A clarifying question is printed as plain text and then again in the boxed callout.
  - At start-up, a failed run is mentioned twice and the open chat twice before you type anything.
  - The parked count is in both the banner and the status bar.
  - The tips line repeats the idle status bar.
- **Tool calls fill the feed.** Each tool call prints its own line, with up to 20 lines in a burst.

## 2. Decisions (user, 2026-10-16)

| Topic | Decision |
|---|---|
| Run names | A lowercase slug of 2–3 hyphen-separated keywords for each run (`newsletter-cta`), written by intake as a new field of the goal it already returns. No extra model call. |
| Name use | Display only. Screens show the name; command hints keep the ID (`phil resume r-4f1c`). |
| Split of facts | The status bar tracks the **task**. The live rows and feed show **which agent is acting**. |
| Status bar during a run | Run name and task, elapsed time, cost against budget. The step dots, model and tokens are dropped. |
| Tool calls while running | Rolling view in the live area: the first two calls, `… N more`, and the latest call. |
| Tool calls once done | One line in the feed: the agent, how many tools it ran, and what it changed, with `/more #n` for the full list. |
| Repeats also fixed | The question shown twice, the start-up lines, and the parked count shown twice. |
| Not in this pass | Hiding internal-step lines (such as "Not sure how big this is · intake decides"), and the mascot. |

## 3. Run names

### 3.1 Where the name comes from

`Goal` (`src/phil/contracts/interface.py`) gains:

```python
name: str | None = Field(
    default=None,
    description="2 to 3 lowercase keywords naming the goal, joined by hyphens, e.g. `newsletter-cta`. Leave null while `open_questions` is non-empty.",
)
```

`phil.runs.naming.run_name(goal, run_id) -> str` decides the name that is stored:

A name is a **slug**: 2 or 3 lowercase keywords joined by hyphens, such as `newsletter-cta`. It always matches `^[a-z0-9]+(-[a-z0-9]+){1,2}$` and is at most 32 characters long.

- **Intake's name is normalised, then used if it is a slug:**
  - trim it and lower-case it;
  - turn spaces and underscores into hyphens;
  - collapse repeated hyphens;
  - use it if the result matches the pattern.
- **Otherwise a slug is built from `goal.objective`:**
  - take its words, lower-cased, keeping only `a-z` and `0-9`;
  - drop stop words (`a an the to of for and or in on with add make create update fix change new some our my`);
  - keep the first three words that are left, joined by hyphens;
  - if fewer than two words are left, take the first two or three words of the objective instead;
  - if that still gives fewer than two words, use `run-<id>` (for example `run-4f1c`);
  - cut it at a hyphen so it fits in 32 characters.
- **The name never fails a goal.** A bad name is only replaced, never rejected. The intake prompt gets one line asking for the field.

Run names can repeat across runs, so the ID stays the run's identity.

### 3.2 Storage

- **New column:** a migration appends `ALTER TABLE runs ADD COLUMN name TEXT` to the list in `src/phil/store/db.py`.
- **When it's written:** `create_run` takes `name: str | None = None` and stores it. The chat passes `run_name(goal, run_id)` when it starts a run from a goal. `phil run` passes the same when it has a goal.
- **Old runs:** runs created before this have `NULL` and are shown by ID, as today.
- **Model:** `RunRecord.name: str | None`.

### 3.3 Display

`phil.ui.runs.run_label(record, *, first: bool) -> str` returns:
- `newsletter-cta (r-4f1c)` on the first mention of a run in a chat;
- `newsletter-cta` on later mentions;
- the ID when the run has no name.

The chat remembers which run IDs it has already labelled (`ChatState.labelled_runs: set[str]`). Within a chat:

- Every rendered mention of a run uses `run_label`. That covers milestone bands that name the run, the run-start and run-end lines, pause notices, the status bar, and the open-chats list.
- Command hints still print the ID: `phil resume r-4f1c`, `phil diff r-4f1c`, `phil attach r-4f1c`.
- `phil show`, `/show` and `phil runs` list both, as `newsletter-cta (r-4f1c)`.
## 4. Status bar and live rows

### 4.1 Status bar during a run

```
roi-calculator @ main │ newsletter-cta · task 2 of 4 · Add signup form │ 3m 12s │ $0.42 / $2.00
```

- **Segments, left to right:**
  1. repo @ branch;
  2. run name, task position and task title;
  3. elapsed time;
  4. cost against budget.
- **Removed:**
  - the step dots and node name (`●●◉○ implement`);
  - the model;
  - the token count. It is already in the run-end line.
- **On a narrow terminal, segments drop in this order (first listed drops first):**
  1. `/btw` and parked;
  2. repo @ branch;
  3. elapsed;
  4. cost;
  5. run name and task.

  Before any segment drops, the task title is shortened with `…`, down to 12 characters. A pause notice never drops.
- **A run with no tasks yet** (planning) shows `newsletter-cta · planning` in place of the task.
- **Paused:** `repo @ branch │ ⏸ newsletter-cta needs you`.
- **The idle status bar is unchanged.**

### 4.2 Live rows above the prompt

The live rows name the agent, not the task:

```
⠋ implementer · editing src/index.html · 42s
  └ explorer · reading build.mjs · 6s
```

- **Main agent:** spinner, role, summary of the current action, elapsed time.
- **Sub-agents:** they keep their `└` lines.
- **Removed:** the task ID and title, now carried by the status bar.
- **Other rows:** `/btw` jobs and goal steps keep their own rows.
- **The `[feed: x]` suffix** is unchanged.

## 5. Tool-call summaries

### 5.1 Bursts

A **burst** is the tool calls of one agent invocation: from the moment the agent starts a step until it returns its result. It also ends if a milestone event is printed for that run. The existing folding of consecutive look-only calls (reads, searches) into one line still applies inside a burst, so a folded group counts as one call for display and as its real number in the count.

### 5.2 While a burst runs

The main agent's live row gets up to four lines of tool calls below it, redrawn in place:

```
⠋ implementer · 42s
    read   src/index.html
    read   build.mjs
    … 5 more
    run    node build.mjs   ok · 0.4s
```

- **With three or fewer calls,** all of them show.
- **With more than three:** the first two, then `… N more` where N counts the calls between them and the latest, then the latest call.
- **Each new call** replaces the latest line and increases N.
- **Sub-agents** keep a single `└` line, as today.
- **The live area** is capped at 6 lines in all (`MAX_LIVE_LINES` goes from 3 to 6). When the cap is reached, the main agent's rolling lines come before sub-agent rows, and the rows that don't fit collapse into `+N more`.
- **While a callout is up,** the live area still collapses to one line, as today.

### 5.3 When a burst ends

One line is printed to the feed, in the muted style:

```
    implementer ran 9 tools · edited src/index.html, build.mjs  (/more #14)
```

- **The count** is every tool call in the burst.
- **The second part:**
  - if the burst edited files, `edited` followed by the edited files, with at most three named and `+N` after that;
  - if it edited none, `ran` followed by the last shell command it ran, cut to 40 characters;
  - if it did neither, nothing: just `implementer ran 4 tools  (/more #14)`.
- **`/more #14`** prints the burst's full list of calls with their results. That's the same detail today's tool lines carry, reached through the existing `/more` path.

### 5.4 Failures stay visible

A tool call that fails is printed as its own line in the feed straight away, in the error style, with today's tool-line format. Failing means any of: an error result, a non-zero exit, a timeout, or a refusal by the permissions policy. It still counts in the burst, and the burst's summary line still follows when the burst ends. A summary never hides an error.

### 5.5 `phil attach` and `/feed`

- **`phil attach`** has no redrawing area. It prints the one-line summary at the end of each burst, plus failure lines as they happen.
- **`/feed <agent>`** filters the summaries and failure lines the same way it filters tool lines today.

## 6. Repeats removed

### 6.1 Clarifying questions show once

- **No plain-text copy:** the copy of the question printed before its callout is removed, and the callout alone asks.
- **A record line after the answer:** the callout disappears, and one record line goes to the feed:

```
? How should visitors sign up for The Human Factor in the CTA section? → Button linking to an external URL
```

- **Text you typed yourself** ("Something else") is recorded as typed. It's cut to the terminal width, ending in `…`.

### 6.2 Start-up lines

- **When the open-chats list is shown, it is the only mention of open chats and their runs:**

  ```
  Open chats:
  1. newsletter-cta · run failed · Add a CTA section below the lead capture form…
  Reopen one? [number / Enter for a new chat] ›
  ```

  - Each row is `run label · run state · goal`, cut to the terminal width with `…`.
  - A chat with no run shows just its goal.
- **Removed:**
  - the separate run line (`r-4f1c failed · phil resume r-4f1c`) when that run belongs to a listed chat;
  - the `N other open chats · phil --resume …` line whenever the list is shown.
- **The run line stays** when its run is not in any listed chat, for example a run started with `phil run`, and it uses the run label.
- **The tips line** (`Type a goal to start · /btw … · /feed … · /help …`) is removed. The idle status bar already says it.
- **A first-ever start** keeps its longer welcome sentence.

### 6.3 Parked items

The banner drops `· N parked`. The status bar keeps it.

## 7. Error handling

- **Names:** a missing or invalid intake name falls back to the built name (§3.1). Naming never fails a goal or a run.
- **Missing names:** a run whose record has no name (old runs, or a run created outside a goal) is labelled by its ID everywhere.
- **Burst state:**
  - lives in the chat's renderer and is dropped when the run ends or the chat closes;
  - if an agent's result never arrives (a stopped or failed run), the burst's summary line is printed when the run-end notice is printed, so no burst is left open.

## 8. Testing

- **Names:** `run_name` keeps a valid intake name, replaces a bad one with the fallback, and builds the fallback correctly, including normalising intake's name, stop words, too few words, the `run-<id>` fallback and the 32-character cut.
- **Contracts:** `Goal.name` is optional, so old outputs still validate.
- **Storage:** the `runs.name` migration runs on an existing database, `create_run` stores the name, and `RunRecord` reads it back.
- **Labels:** `run_label` gives the first and later mentions, and the ID when there's no name. Command hints keep the ID.
- **Status bar:** the segments during a run, the planning state, the pause, and what drops first on narrow terminals, including the task-title shortening.
- **Live rows:** the agent-only row, the rolling view with three or fewer calls and with more, and the 6-line cap with collapse.
- **Bursts:**
  - the counting (folded groups count as their real number);
  - the summary line's edited, ran and neither forms;
  - `/more #n` printing the full list;
  - a failure printed straight away and still counted;
  - a burst closed by the run's end.
- **Callouts:** no plain-text copy of the question; the record line after an answer and after typed text.
- **Start-up:**
  - the merged open-chats list;
  - the run line kept only for a run outside the list;
  - no tips line;
  - the first-start sentence kept;
  - no parked count in the banner.
- **Existing tests:** UI tests that check exact text (`tests/ui`, `tests/chat`, `tests/cli/test_banner_startup.py`) are updated alongside each change.
