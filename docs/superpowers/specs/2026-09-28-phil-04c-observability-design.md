# Phil 4c — Observability and Reliability Design

**Status:** approved in conversation 2026-09-28.
**Scope:** plan 4c — accurate token/cost accounting, tool-call telemetry, visibility (`phil runs`, run summary, `phil show`, chat toolbar, budget warnings), model-call timeouts and retries, `/show` / `/more` / `/park` in the chat, run-summary cleanup. Deferred: a lean read-only architect; provider-agnostic models (before public release).

## 1. Problem

- **Undercounted tokens.** `invoke_agent` sums `usage_metadata` over the messages an agent returns. Model calls made inside a deep agent's sub-agents are not in that list, so they are never counted; the budget guard is lenient as a result (3a follow-up).
- **Missing cost.** Cost comes from `response_metadata["cost"]`; when a provider doesn't report it, Phil records `$0` (a GPT-6 Sol run showed `$0.14` for ~200k tokens).
- **No tool-call counts** — nothing shows which agent used which tools, or that a model skipped them (the first live run's failure mode).
- **No timeout** on model calls; a stuck provider blocks a step indefinitely. A 200 response carrying an error is not retried.
- **Detail is hard to reach:** logs, packets and reviewer output live in the run directory with no command to browse them; the summary repeats open issues and can contain raw newlines/markdown.

## 2. Decisions (user, 2026-09-28)

| Topic | Decision |
|---|---|
| Scope | All items in one plan. |
| Visibility | `phil runs` + run summary; `phil show <run>` breakdown; live cost in the chat toolbar; budget warnings. |
| Cost source | Use the provider-reported cost when present; otherwise compute tokens × the model's published price (OpenRouter model list, cached daily); mark computed costs as estimates (`~`). Unknown when neither is available. |
| Timeout | 180 s per model call (configurable); one retry on timeout / transient error, then the step fails as today. |
| Budget warning | One warning at 80% of `max_tokens` or `max_cost_usd` (configurable); the pause at 100% stays. |

## 3. Design

### 3.1 Accounting

- **`UsageCollector`** (a LangChain callback handler) is passed to every agent invocation through the run config. LangChain propagates callbacks to nested runs, so it sees every chat-model call — top-level and sub-agent. Per model call it records the model name, input/output tokens (`usage_metadata`) and the reported cost (`response_metadata["cost"]` when present). It counts tool starts by tool name, excluding the structured-output tool (named after the output contract).
- **Cost resolution** per model call: reported → `reported`; else price × tokens from the `PriceBook` → `estimated`; else `unknown` (cost 0, flagged). An agent call's cost source is the weakest of its model calls (`unknown` > `estimated` > `reported`).
- **`PriceBook`**: loads OpenRouter's public model list (`GET https://openrouter.ai/api/v1/models`, no key), caches it in `~/.phil/cache/openrouter-models.json`, refreshes after 24 h; network failure falls back to the cached copy or to "no price". Only `openrouter:` models are priced (provider-agnostic pricing comes with provider-agnostic models). Tests inject the list; no network.
- **Storage:** a new `calls` table (one row per model call: telemetry id, model, input/output tokens, cost, cost source, created_at). `telemetry` gains `chat_id`, `tool_calls` (JSON `{name: count}`), `retries` (transient retries in `call_with_retry`), `cost_source`, `model_calls`. Fakes (scripted agents) report no callback calls; `invoke_agent` falls back to the returned messages' usage so tests keep their numbers.
- **Chat cost:** `AgentContext.chat_id` tags chat-layer telemetry (intake, architect, critic, `/btw`); a chat's cost = its chat telemetry + telemetry of runs with `runs.chat_id` = the chat.
- **Budget guard** uses the new totals (now including sub-agent calls).

### 3.2 Visibility

- **`phil runs`**: accurate tokens/cost; `~$` when any part is estimated, `$?` when any part is unknown.
- **Run summary** (`summary.md`): a `## Usage` section — totals plus one line per layer/role (calls, tokens in/out, cost, tool calls).
- **`phil show <run>`**: run header (keyword, state, base..branch, tasks done), tasks with status, the usage breakdown table (layer, role, calls, model calls, tokens in/out, cost, tool calls, retries), open issues (deduplicated), and numbered detail refs: summary, worker log, test logs, reviewer/tester outputs, packets (most recent first, capped). `phil show <run> <n>` prints ref *n* in full.
- **Chat:** `/show` = `phil show` for the chat's run; `/more <n>` expands ref *n* from the most recent notice that listed refs (completion notice, `/btw` answer details, `/show`). The toolbar shows the chat's running cost (`$0.42` / `~$0.42`), refreshed when jobs finish and on each run progress event.
- **Budget warning:** when a run's tokens or cost first reach `[run] warn_at` (default 0.8) of the limits, the engine appends one `budget_warning` event (tokens, cost, limits); the chat's watcher posts it and the chat prints `r-7f3a has used 80% of its budget ($1.61 of $2.00).` `phil attach` renders it too.

### 3.3 Reliability

- **Timeouts:** the agent factory builds the chat model object itself (instead of passing a model string) with the configured timeout — `[run] model_timeout_s = 180` — converted to each provider's unit (OpenRouter takes milliseconds), and disables the provider SDK's own retries so Phil's retry policy is the only one.
- **Retries:** `is_transient` also treats timeouts from the provider SDKs and 200-with-error responses (an error body on a successful status, with a transient code such as 429/5xx) as transient. `call_with_retry` does 2 tries total for these (plus its existing backoff for other transient errors) and reports how many retries happened.

### 3.4 Chat additions

- **`/park <note>`**: records a parked item (`raised_by="user"`, `why_not_now="parked from chat"`, source = the chat, `run_id` = the chat's run if any), prints `Parked P-012.`; the header/toolbar parked count updates.
- **`/show`, `/more <n>`** as above.

### 3.5 Summary cleanup

- Open issues are deduplicated by (task id, normalized note) across tester and review rounds, keeping the highest severity.
- Notes are rendered on one line: newlines collapsed, markdown emphasis/backticks stripped, length capped (full text stays in the artifacts).

## 4. Testing

- `UsageCollector` with synthetic callback events: nested runs, tool starts, the structured-output tool excluded, errors.
- `PriceBook` with a fixture model list and a fake clock (fresh cache, stale cache refresh, fetch failure → cached/none).
- `invoke_agent`: callback totals win over message totals when present; cost sources; `calls` rows; tool counts; retries; chat_id.
- Timeouts: the factory passes the right kwarg per provider (checked on the constructed model, no network); `is_transient` on constructed SDK exceptions.
- Budget warning: engine emits once at the threshold; watcher → chat prints it.
- `phil show` / `/show` / `/more` / `/park`: CLI and controller tests.
- Live (user): a real chat + run; `phil show` shows reported or estimated cost and sub-agent calls counted.
