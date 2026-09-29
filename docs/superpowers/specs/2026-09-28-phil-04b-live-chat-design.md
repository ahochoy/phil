# Phil 4b — Live Chat Design

**Status:** approved in conversation 2026-09-28; amends `2026-09-23-phil-v1-design.md` §3 and §6.
**Scope:** plan 4b. Observability items (sub-agent token counting and cost reconciliation, tool-call telemetry, OpenRouter timeout and 200-with-error, `phil show` / `/more`, `/park`) move to plan 4c.

## 1. Problem

The first live chat (2026-09-28) worked but felt mechanical, transactional and disconnected:

- While planning, the only feedback was static text ("Planning…").
- After approval the run went to the background and the chat lost it; following it took a second terminal and `phil attach`.
- Run approvals and questions had to be answered in that second terminal.

Principles (user): communicative, not chatty; always obvious what Phil is doing (status indicators, affordances); never an extra step to answer a basic question.

## 2. Decisions

| Topic | Decision |
|---|---|
| Unit of work | **One chat = one goal at a time.** A chat carries a goal through intake, plan, approval, its run, pauses and completion. Parallel work happens in more windows (terminal splits, multiplexers), not inside one chat. |
| After completion | The chat shows a completion notice and accepts the next goal, which becomes its new unit of work. |
| Concurrency model | `prompt_toolkit` prompt on the main thread + worker threads (a small pool for model calls, one watcher thread for the run) + one event queue. Not asyncio (Phil's core is synchronous; asyncio would add an async layer over threads for little gain) and not Rich `Live` (fragile with line input). |
| Toolbar | Always-visible bottom toolbar: current step with a spinner and elapsed time, the run's progress, and a pause flag. |
| Pauses | Flag immediately (a notice above the prompt + toolbar), ask the question at the next idle prompt; `/answer` jumps to it any time. |
| `/btw` | Side questions answered while work continues, read-only: sees the goal, plan, run status and recent events, pending pause, and the repo snapshot. Never changes the plan or the run. |
| Coming back | Chats can be reopened: `phil` lists open chats in the repo; `phil --resume <chat>` reopens one; `phil --new` starts fresh. `phil attach` keeps working for runs. |

## 3. Architecture

```
PromptSession (main thread) ── bottom toolbar: reads ChatState on each redraw
      │ input
      ▼
ChatLoop ─ dispatch ─► goal job (thread pool): intake → architect → critic → revise ─┐
      ▲                /btw job (thread pool)                                          ├─► ChatEvents queue
      │                RunWatcher (thread): tails runs/<id>/events.jsonl ──────────────┘
      └── drains the queue between inputs; prints notices above the prompt
```

- **`ChatState`** (`chat/state.py`): lock-protected state for the chat's unit of work — `goal`, `stage` (`idle`, `intake`, `questions`, `planning`, `approval`, `running`, `paused`, `done`), `step` + `step_started` (spinner and elapsed time), current draft, `run_id`, the run's latest node, `tasks_done/total`, pending pause payload, in-flight `/btw` count. Workers write it; the loop and toolbar read it.
- **Workers** (`chat/workers.py`): a thread pool for model calls and one `RunWatcher` thread. Everything reports through a single `queue.Queue` of typed `ChatEvent`s (`step`, `goal_ready`, `plan_ready`, `job_failed`, `btw_answer`, `run_progress`, `run_paused`, `run_resumed`, `run_done`, `worker_lost`).
- **`ChatLoop`** (`chat/loop.py`): replaces the blocking `ChatController.run`. Each turn: drain the queue (render notices), pick the prompt for the current stage (`you ›`, the question round, `Approve? [y / edit / n] ›`, the pause question), read input, dispatch. Keeps the 4a pieces: `intake`, `Planner`, `launch_problems`, the snapshot, `_start`, the transcript.
- **Toolbar** (`ui/toolbar.py`): a pure function `ChatState, now -> formatted text`, e.g. `⠋ Architect drafting · 12s`, `r-7f3a · CALC 1/2 · implement · 3m`, `⏸ r-7f3a needs you (/answer)`. The spinner frame is derived from elapsed time, so no animation thread; the session redraws on a ~0.5 s refresh interval.
- **Output:** Rich still formats; output is captured as ANSI and printed via `prompt_toolkit`'s `print_formatted_text(ANSI(...))` inside `patch_stdout`, so notices appear above the prompt without clobbering typed input. One `ChatOutput` adapter owns this.
- **Chat ↔ run link:** the `runs` table gains a nullable `chat_id` column (migration); `prepare_run(..., chat_id=)` sets it. The chat session directory gains `state.json` (goal, stage, draft plan version, run id, base sha), written on every stage change, so a reopened chat can rebuild itself. The transcript (`transcript.jsonl`) stays the lossless record.

## 4. Flows

- **Goal:** a non-slash input at `idle` starts the goal job; the toolbar spins through intake → architect → critic → revision. Open questions print and the prompt collects answers (≤2 rounds, `go` skips) — the next intake call runs as a job. `plan_ready` prints the plan (4a plan view) and the prompt becomes `Approve? [y / edit / n]`. `edit` collects feedback and starts a revise job. While a goal job runs, `/btw`, `/runs`, `/help` work; typing a new goal asks `Replace the current goal? [y/n]` (replacing discards the in-flight result when it lands — a running model call cannot be interrupted).
- **Run:** on `y`, the 4a launch checks run (config re-read, models, API keys, test command), then `prepare_run(chat_id=…)` + spawn. The `RunWatcher` starts; the toolbar shows node, task n/m and elapsed.
- **Pause:** a `run_paused` event prints `⏸ r-7f3a needs you: <summary>` immediately and flags the toolbar. At the next idle prompt the question is asked with its options (plus a hint prompt for `retry`); the answer spawns a resume worker (reusing `phil attach`'s decision path and the 3b one-worker-per-run guards). `/answer` jumps to it any time. If the run leaves `escalated` because someone answered elsewhere, the pending question is dropped.
- **Done:** `run_done` prints the outcome, tasks done, open issues, and `phil diff <run>` + summary path; the chat returns to `idle` for the next goal.
- **`/btw <question>`:** a lean-context, read-only `btw` agent (role `orchestrator`) answers on the thread pool; its input contract carries the goal, the current plan (if any), the run status, the last N run events, the pending pause, and the question; it has read-only file tools on the goal's base-commit snapshot. The answer is a bounded `Brief` printed above the prompt. It never changes the plan or the run.
- **Reopen:** `phil` in a repo lists open chats (goal set and run not finished, or finished but its completion not yet shown) with goal, run and state, and offers to reopen one or start new. `phil --resume <chat>` reopens directly; `phil --new` skips the list. Reopening prints the goal, plan headline and run status, restarts the watcher, and asks any pending question.

## 5. Error handling

- A failing job (provider error, `ContractViolation`, `PacketTooLarge`) posts `job_failed`: the chat prints `Phil couldn't finish that: …` and the session dir, and the stage returns to where the user can retry or give a new goal.
- Ctrl-C at the prompt clears the line; during a goal job it cancels the goal (result discarded on arrival; toolbar shows `cancelling…`). Ctrl-D exits; if a run is still working the chat says so and how to reopen (`phil --resume <chat>`).
- The watcher never crashes on a missing or partial log; it retries next tick. A dead worker on a running row posts `worker_lost` with the `phil resume` hint (`/answer` offers continue).
- Transcript/state write failures show a muted warning; they never end the chat.
- No TTY (piped input, CI): fall back to the 4a line-mode loop — same dispatch, no toolbar — which is also what most tests drive.

## 6. Testing

- Unit, no terminal: `ChatState` transitions; toolbar rendering from fixed states and times; `RunWatcher` against a temp `events.jsonl`; the `btw` contract and agent spec; the `chat_id` migration; `state.json` round trip.
- `ChatLoop` with a fake IO that feeds inputs and pumps the queue deterministically (jobs run inline or awaited): goal → plan → approve → run started; pause while idle → question → answer → resume spawn; completion → next goal; `/btw` during planning; replacing a goal mid-planning; answered-elsewhere drops the question; reopen from `state.json`.
- One `prompt_toolkit` smoke test with pipe input and dummy output: the session starts, renders the toolbar, and exits cleanly.
- Live check (user): a real chat in the scratch repo covering toolbar, a pause answered in the chat, `/btw`, and reopening.

## 7. Out of scope (4c or later)

Token/cost accuracy and tool-call telemetry, OpenRouter timeouts and 200-with-error handling, `phil show` / `/more`, `/park`, streaming model tokens into the chat, multiple goals per chat, provider-agnostic models (before public release).
