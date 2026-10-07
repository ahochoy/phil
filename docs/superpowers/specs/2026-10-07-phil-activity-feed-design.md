# Phil: Live Activity Feed

**Status:** approved in conversation, 2026-10-07. This is the first slice of roadmap M5, "Terminal experience and visibility".

## 1. Problem

Today a chat that follows a run sees very little:

- **What it sees.** A once-a-second summary taken from the run's database row: the stage, the state, how many tasks are done, tokens and cost. On top of that come occasional notices: a budget warning, a changed test command, an escalation.
- **What it doesn't see.** Everything the agents actually do: the files they read and edit, the commands they run, test results, retries. That activity is logged to files the chat never reads.
- **The effect.** During a long implement step the chat sits on `implement · 2/3` for minutes.

This breaks a standing product principle. In the user's chat UX feedback, it must always be obvious what Phil is doing: background runs report back into the chat that started them, and Phil is communicative, not chatty.

## 2. Decisions (user, 2026-10-07)

| Topic | Decision |
|---|---|
| First M5 slice | The live activity feed. Chat chrome, callouts, failure categories and visual proposals come later. |
| Default detail | One compact line per tool call, with milestones highlighted, plus a live "now" row. |
| Live row placement | Its own row just above the input, not inside the toolbar. |
| Details on demand | Every line that has detail (an edit's diff, a command's output, a failure report) carries a `#n` reference. This may be pulled back later if it's too noisy. |
| Agent narration | Not shown. Tool calls only; the narration stays in the transcript. |
| Transport | A separate `activity.jsonl` per run for tool calls. Milestones go in the existing `events.jsonl`. |

Mockups from the session are in `.superpowers/brainstorm/` (not committed): `feed-layout.html` and `feed-details.html`.

## 3. Design

### 3.1 Data flow

```
worker                                   chat / phil attach
──────                                   ──────────────────
ActivityRecorder (agent middleware) ─┐
engine gate runs ────────────────────┼─> runs/<id>/activity.jsonl ─┐
                                     └─> runs/<id>/activity/<seq>.txt (detail)
engine nodes (milestones) ─────────────> runs/<id>/events.jsonl ───┼─> RunWatcher (offset tail)
                                                                   │      │
                                                                   │      v
                                                                   └─> feed renderer -> lines, live row
```

### 3.2 Recording tool calls

- **The recorder.** `ActivityRecorder` is agent middleware (a `wrap_tool_call` / `awrap_tool_call` pair). It's added to every agent Phil builds: the deep, light and lean harnesses. It's also added to the deep agent's general-purpose sub-agent, whose middleware must be listed explicitly because it inherits only replacements.
- **Records.** Around each tool call the recorder writes two JSON lines to `runs/<id>/activity.jsonl`:
  - **start:** `{"seq", "ts", "phase": "start", "task", "role", "tool", "summary"}`.
  - **end:** `{"seq", "ts", "phase": "end", "duration_ms", "result", "ok", "detail"}`.
- **Fields.**
  - `seq` is a per-run counter that only goes up. It's shared by both phases of one call, and it's also the `#n` the user types.
  - `summary` is a short, tool-specific description: `run pytest -q`, `edit calc.py`, `read calc.py`, `grep "divide" src`.
  - `result` is a short outcome: `→ 7 passed`, `→ 2 failed`, `+12 −1`, `exit 2`, or an error's first line.
  - `detail` names the detail file, or is null.
- **Detail files.** `runs/<id>/activity/<seq>.txt` holds what is behind a reference:
  - a command's full output;
  - an edit's or write's unified diff;
  - a tool error's message.

  Reads, `ls`, `glob` and `grep` get no detail file. The content is capped at the limits `detail_text` already applies (2000 lines or 200,000 characters).
- **Context.** The task id and role come from the agent's existing invocation context (`AgentContext` / the role on the spec). The recorder is constructed with the run's paths and that context.
- **Engine test runs.** The engine runs tests itself for the red, green, baseline, final and tester checks (`Engine._test`). Those runs go through the same recorder API (`record_command`), so they appear as `gate pytest -q → 1 failed  #n` lines.
- **Ordering and concurrency.**
  - Records are appended under a process-local lock.
  - `seq` comes from a counter held by the recorder. On resume it is seeded from the last `seq` in the file.
  - Only one worker runs a given run at a time (the claim guarantees it), so no cross-process lock is needed.

### 3.3 Milestones

The engine appends new event kinds to the existing `events.jsonl`. Each one also carries the latest activity `seq` at that moment, so the renderer can order it among the tool lines.

| Kind | Written at | Payload |
|---|---|---|
| `task_started` | `pick_task` | task id, title, role, attempt |
| `gate` | `verify` and the baseline | name (red, green, baseline, final), passed, summary, detail seq |
| `attempt_failed` | `_failed_attempt` | task, attempt, limit, the first problem, `retrying` (bool) |
| `task_done` | `commit` | task, attempts, files changed, elapsed, cost |
| `verdict` | tester and reviewer | role, outcome (passed / issues), counts |

The existing kinds (`node`, `state`, `escalation`, `outcome`, `worker`, `test_cmd_changed`) are unchanged.

### 3.4 Following: the watcher

- **Reading.** `RunWatcher` reads both files from a remembered byte offset on every poll (1s): `events.jsonl` through `EventLog.read(offset)`, and `activity.jsonl` through the same complete-line rule.
  - A line counts only once it ends with a newline.
  - A malformed complete line is skipped with a debug log, and reading continues.
- **Starting position.** A watcher starts at the current end of both files, so a reopened or resumed chat doesn't replay old activity. The current live step is the exception: the newest start record that has no end record yet is seeded so the live row shows at once.
- **Posting.** New records are posted to the chat as `ChatEvent("activity", …)` and `ChatEvent("milestone", …)`. The existing `run_progress`, notices, escalation and `run_done` handling is unchanged.
- **Bursts.** At most 20 tool lines are posted per poll. The rest are summarised in one line: `… 37 more reads`, or `… 12 more steps` when the kinds are mixed.

### 3.5 Rendering

- **A shared renderer** in `src/phil/ui/feed_view.py` turns records into Rich renderables. It's used by the chat controller and by `phil attach` (`render_event`).
- **Tool lines** are indented 4 cells under their task and styled `phil.muted`:
  - `read calc.py`
  - `edit calc.py +4 −1  #13`
  - `run pytest -q → 2 failed · 1.8s  #14`
  - `gate pytest -q → 7 passed  #15`

  Consecutive reads within one step fold into a single line: `read calc.py · tests/test_calc.py`.
- **Fitting to width.**
  - Summaries are cut with `…` so a tool line never wraps.
  - The `#n` and the result are never cut.
  - At narrow widths (under 60 cells), the duration is dropped first.
- **Milestone bands** have a left border and a tinted background where the terminal supports it, and a plain coloured marker otherwise:
  - task started: `▸` in `phil.brand`;
  - task done and passing gates: `✓` in `phil.gate.pass`;
  - failed attempts and failing gates: `✗` in `phil.gate.fail`;
  - waiting for the user: `phil.warn`.

  Bands are never dimmed and never cut short; they wrap if they need to.
- **References.** Every line with a detail file shows `#<seq>` in `phil.id` at the end.

### 3.6 The live row

- **Where it lives.** It's part of the chat's prompt message: one line above the input, redrawn by the existing `refresh_interval=0.5`.
- **What it shows.**
  - While a tool is running: spinner, task, role, the start record's summary, and the time since it started. For example, `⠹ CALC-002 · reviewer · read README.md · 8s`.
  - Between tool calls: the current stage label (`Picking the next task`, `Verifying`, `Committing`).
  - With no active run: it isn't shown.
- **Width.** It is fitted to the terminal width like the toolbar, so it never wraps.
- **`phil attach`** has no live row and no input. It prints the feed lines only, plus its existing escalation prompts.

### 3.7 Opening details

- **In the chat:** `/more #14` prints the detail file through `detail_text`, inside a bordered panel titled with the line's summary.
  - `/more 2`, without the `#`, keeps its current meaning: item 2 of the last `/show`, completion notice or `/btw` answer.
  - An unknown seq, or one with no detail, prints `#14 has no details.`
- **From any terminal:** `phil show <run> #14` does the same.
- **When it works.** References keep working after a run ends, until `phil clean` removes the run folder.

### 3.8 Errors and edge cases

- **Recording never fails a run or a tool call.** If writing a record or a detail file raises, the recorder logs one warning to the worker log and disables itself for the rest of that worker. The tool's own result or exception passes through unchanged.
- **A tool that raises** is recorded with `ok: false` and the error's first line as `result`. The exception is then re-raised.
- **Secrets.** Phil has no output redaction today, and this design adds none. Tool output can't contain provider keys, because `child_env` already strips secret-named variables from every command Phil runs, unless the user names them in `[shell] pass_env`. Detail files hold the same text the agent already sees, and they live under `~/.phil`, like the existing shell logs and transcripts.
- **Old runs and missing files.** A run started before this change, or one whose activity file is missing or unreadable, shows no tool lines. Its milestones still show.
- **A killed worker.** A start record with no end record stays "in progress" only while the worker is alive. The live row falls back to the stage label once the watcher sees the worker is gone.
- **Encoding and line endings.** All files are written as UTF-8 with `\n` line endings, which the existing encoding audit test enforces.

### 3.9 Testing

- **Recorder:**
  - start and end records around a fake tool call;
  - detail files for a command and an edit;
  - a tool error recorded and re-raised;
  - a write failure leaves the tool call unaffected and disables recording;
  - `seq` is seeded on resume.
- **Engine milestones:** a scripted run writes `task_started`, `gate`, `attempt_failed` (with a retry), `task_done` and `verdict`, in order, with seqs.
- **Renderer** (fixed record lists, compared against expected text):
  - folded reads;
  - bands;
  - `#n`;
  - fitting to width at 40 and 120 cells;
  - burst collapsing;
  - folded reads across different tasks.
- **Watcher:**
  - resumes from its offset;
  - skips a half-written final line;
  - skips a malformed line;
  - doesn't replay on a reopened chat;
  - seeds the live step.
- **Chat, end to end, with a scripted agent:** the expected feed, the live row text, and `/more #N` printing the detail. `/more 2` keeps its meaning.
- **CLI:** `phil show <run> #N`, and feed lines in `phil attach`.

## 4. Out of scope

- The welcome banner, the status-bar redesign, and the sub-agent bar.
- Question and approval callouts, and failure categories.
- Visual proposals for UI work.
- A full-screen app.
- Agent narration in the feed.
- Live streaming of a command's output while it runs. Output shows only after the command finishes, through `#n`.

## 5. Done means

- During a run, the chat shows compact tool lines under each task, highlighted milestones, and a live row that updates while tools run.
- `/more #n` and `phil show <run> --step n` open the detail.
- `phil attach` shows the same feed.
- CI passes on Ubuntu, macOS and Windows.
- A live run, checked by the user, feels "obvious what Phil is doing" without flooding the screen.

## 6. Plan-time amendments (2026-10-07)

These came out of reading the code while writing the plan. They override the sections above where they differ.

- **A1: a callback, not middleware.** Tool calls are recorded by a LangChain callback (`ActivityCallback`), which `invoke_agent` adds next to the existing `UsageCollector`. That replaces the agent middleware in §3.2.
  - Callbacks already reach every harness and every nested sub-agent run.
  - It needs no change to how agents are built, and scripted test agents can fire it.
- **A2: no per-task cost in `task_done`.** It carries the task and the number of files changed. The elapsed time is worked out by the renderer from `task_started`. The run's running cost stays in the toolbar.
- **A3: `phil show <run> --step N`.** This replaces `phil show <run> #N` from §3.7, because `#` starts a comment in shells. In the chat, `/more #N` is unchanged.
- **A4: `gate` milestones only for passing gates** (red, green, check). A failing gate shows as `attempt_failed`, with its retry count. The engine's own test runs still appear as `gate …` tool lines, whether they pass or fail.
- **A5: `phil attach` and old activity.** `phil attach` replays milestones from the start, as it already does for events. Tool lines start from when it attaches.
