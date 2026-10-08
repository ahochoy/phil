# Phil: Active Agents in the Live Row, and a Feed Filter

**Status:** approved in conversation, 2026-10-08. This is the fourth slice of roadmap M5 (the "sub-agent bar"). It builds on the activity feed (PR #30) and the status bar (PR #32). The chat stays inline (decided 2026-10-08), so there's no switching between per-agent panes.

## 1. Problem

- **One live row.** It shows only the newest open tool call of the run's agent.
- **Sub-agents.** When the implementer starts a sub-agent (the deep agent's `task` tool), the sub-agent's tool calls appear in the feed under the implementer's role. Nothing marks them as the sub-agent's.
- **Side jobs.** A `/btw` question or a goal step running alongside a run is invisible apart from a `/btw ×N` count in the status line.
- **One agent's work.** There's no way to follow just one agent's lines in a busy feed.

## 2. Decisions (user, 2026-10-08)

| Topic | Decision |
|---|---|
| Purpose | What's running right now, and filtering the feed by agent. Per-agent cost and a pipeline view are not included. |
| Placement | The live row grows to one line per active agent (option B). With one agent working, it's today's single line. |
| Filter | `/feed <agent>` shows only that agent's tool lines (milestones always show). `/feed` with no agent shows everything and reports how many lines were hidden. |

Mockups: `.superpowers/brainstorm/` (not committed), `subagent-bar.html`.

## 3. Design

### 3.1 What the live row shows

One line per active agent, in this order, up to 3 lines and then `+N more` on a fourth:

1. **The main agent.** During a run, the run's agent and its current step, as today (`⠹ CALC-002 · implementer · run pytest -q · 2s`). With no run, the goal step being worked on (`⠹ Architect drafting · 12s`).
2. **Its sub-agents.** Each is indented with `└`: `⠼  └ sub-agent · read tests/test_calc.py · explore tests · 6s`. That's the open tool call's summary, then the `task` call's description (cut to fit), then the time since that `task` call started. A sub-agent with no open tool call shows `└ sub-agent · working · <description> · <time>`.
3. **Side jobs.** Each `/btw` question still being answered: `⠧ /btw · "why did CALC-001 take 3 tries?" · 4s`, with the question cut to fit. A goal step running alongside a run shows as `⠹ Architect drafting · 12s`.

**Spinner colours:** the main agent is `phil.agent` (cyan), sub-agents are `phil.sub` (a new style, magenta), and side jobs are `phil.warn` (yellow). Every line uses the braille spinner and redraws on the prompt's 0.5 s refresh.

**Width:** every line is fitted to the terminal width on its own, so none of them wrap.

**When the row is empty:** with nothing running, the live row is hidden, as today. With no run, it shows only a goal step or a `/btw` line.

### 3.2 Tagging sub-agent activity

`ActivityCallback` gains three handlers, `on_chain_start`, `on_chat_model_start` and `on_llm_start`. Each records `run_id → parent_run_id` into a parent map, so the callback can walk any tool call's ancestry. LangChain passes `parent_run_id` to every callback.

- **`task` calls.** When a `task` tool call starts, its run id is remembered as a sub-agent root, with that call's `seq` and its description (`args["description"]`).
- **Other tool calls.** When another tool call starts, the callback walks its ancestors to the nearest open sub-agent root. If it finds one, the start and end records gain `"sub_id": <the task call's seq>` and `"sub": <description>`. The record keeps its role, which is the parent agent's.
- **Clean-up.** When a `task` call ends, its root is removed. The parent map is cleared when the callback's agent call finishes (on the outermost chain's end, or when `invoke_agent` returns), so it never grows without limit.
- **Never raises.** All bookkeeping is wrapped in the callback's existing guards. Untagged records (old runs, no sub-agent) behave as today.
- **Nesting.** A sub-agent that starts its own sub-agent is shown one level deep. Its calls are credited to the nearest open `task` call.

### 3.3 Tracking every open call

- **The watcher.** `RunWatcher` keeps every open start record (`seq → record`) across polls, not just the newest. It posts `ChatEvent("live_agents", {"main": {...} | None, "subs": [{...}, ...]})` whenever that set changes.
  - `main` is the newest open record without a `sub_id` whose role isn't `engine`. If none, it's the newest `engine` record (a gate test run).
  - `subs` lists each open `task` call (by `sub_id`), with its newest open inner record if there is one.
- **The existing `live_step` event.** It remains for compatibility, and carries `main`.
- **When everything clears.** A new worker's `spawn`, a lost worker or the end of the run clears everything, as the live step is cleared today.
- **The controller** keeps `subs` in `ChatState` (`ToolbarView.subs`), and `/btw` jobs as `ToolbarView.side`. Each `/btw` entry is `(question, started)`. It is added when the job is submitted and removed when its answer or failure lands.
- **The status line's `/btw ×N`** stays as it is.

### 3.4 The feed filter

- **`/feed <agent>`.**
  - `<agent>` is one of `implementer`, `tester`, `reviewer`, `architect`, `sub-agent` or `engine`.
  - Afterwards only that agent's tool lines print. Milestone bands always print.
  - Sub-agent lines belong to the agent that started them, so `/feed implementer` includes them, and `/feed sub-agent` shows only lines with a `sub_id`.
  - It prints `Showing only the <agent>'s lines. /feed to show everything.`
- **Lines being hidden.** While a filter is on, the live row's first line ends with `[feed: <agent>]`. Other agents still show in the live row, because it describes what's running.
- **`/feed` with no agent.** It clears the filter and prints `Showing everything again. <n> lines from other agents were hidden: /show <run> to see them.` If nothing was hidden, it prints `Showing everything again.` Hidden lines are counted, not replayed.
- **An unknown agent.** It prints `Pick one of: implementer, tester, reviewer, architect, sub-agent, engine.`
- **Scope.** The filter belongs to this chat window and is cleared when the run ends. It isn't saved when a chat is reopened.
- **Folding.** Filtering happens before folding and the burst cap, in `_on_activity`, so folded reads and `… N more` count only shown lines.
- `/help` lists `/feed`.

### 3.5 Testing

- **Callback:**
  - a tool call nested under an open `task` call (through chain and model runs) is tagged with that call's `sub_id` and `sub`;
  - a call outside one isn't tagged;
  - nested `task` calls credit the nearest one;
  - the map is emptied when the agent finishes;
  - bookkeeping errors never raise.
- **Watcher:**
  - several open calls give the expected `main` and `subs`;
  - an end record removes its call;
  - `spawn` clears everything;
  - an `engine` record is `main` only when nothing else is open.
- **Live row:**
  - one agent renders exactly as today;
  - main plus one sub;
  - main, a sub and a `/btw`;
  - five agents, so the last line is `+N more`;
  - widths 40 and 120;
  - the spinner styles;
  - the `[feed: x]` tag.
- **Feed filter:**
  - each agent name;
  - `sub-agent` and `engine`;
  - an unknown name;
  - the hidden count, and the "nothing hidden" message;
  - milestones still print;
  - it resets when the run ends.
- **End to end:** a scripted run whose implementer turn fires a `task` call with a nested tool call, using `fire_tool` plus a chain-start event. The live row shows the sub-agent line, and the feed line is tagged.

## 4. Out of scope

- Per-agent cost.
- A pipeline view.
- Full-screen agent panes.
- Replaying hidden lines.
- Saving the filter across reopened chats.

## 5. Done means

- During a run, the live row shows every active agent, with sub-agents indented under their parent agent and `/btw` questions as their own lines.
- `/feed <agent>` filters the feed, and `/feed` restores it with a count of hidden lines.
- CI passes on Ubuntu, macOS and Windows.
- The user checks it live.
