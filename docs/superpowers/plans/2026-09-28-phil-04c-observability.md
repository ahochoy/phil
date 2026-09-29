# Phil Plan 4c: Observability and Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Count every model call (including deep-agent sub-agents), cost it (reported, else estimated from published prices), record tool calls and retries, show it all (`phil runs`, run summary, `phil show`, chat toolbar, budget warnings), put a timeout on every model call with one retry on transient failures, and add `/show`, `/more`, `/park` to the chat plus a cleaner run summary.

**Architecture:** A LangChain callback (`UsageCollector`) rides along every agent invocation and records each chat-model call and tool start; `invoke_agent` resolves each call's cost through a cached `PriceBook`, writes one `calls` row per model call and an enriched `telemetry` row per agent call. Totals come from SQL over those tables. The agent factory builds chat-model objects with timeouts. Visibility is plain rendering over the totals.

**Tech Stack:** Python 3.14, LangChain callbacks (`langchain_core.callbacks.BaseCallbackHandler`), `urllib.request` (price list fetch — no new dependency), SQLite migrations, rich, existing CLI/chat.

**Spec:** `docs/superpowers/specs/2026-09-28-phil-04c-observability-design.md` (binding).

**Deviation from the spec, decided here:** the spec says the model timeout is configured as `[models] timeout_s`, but `[models]` maps role names to model strings and rejects unknown keys. The timeout lives in `[run] model_timeout_s = 180` (it applies to chat agents too); the budget warning threshold is `[run] warn_at = 0.8`.

## Global Constraints

- Python `>=3.14`; `uv`; `uv run pytest -q` (parallel; `-n 0` for serial debugging). Keep dependencies current; add none unless required.
- `phil.cli.main`, `phil.agents.invoke`, `phil.packets`, `phil.chat.*`, `phil.ui.*` must not import `langgraph`, `langchain*`, or `deepagents` at module level. The collector module imports `langchain_core` and is imported lazily inside `invoke_agent`.
- Tests never touch the real `~/.phil`, never read global git config, never call a real model, and **never touch the network** (the price list is injected or read from a fixture; any fetch is behind an injectable function).
- Chat rules from 4b hold: worker threads never print; each job uses its own SQLite connection; only the main thread prints.
- Escape user/agent text printed through rich (`escape(repr(x))`, never `escape(x)!r`; TOML section names escaped).
- Existing numbers in tests stay meaningful: scripted agents (`ScriptedAgentFactory`, `FakeAgentFactory`) make no LangChain callback calls, so `invoke_agent` falls back to the returned messages' usage for them.
- Every commit message ends with a blank line then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

```
src/phil/store/db.py              modify: migrations (telemetry columns, calls table)
src/phil/store/telemetry.py       modify: TelemetryRow fields, record() returns id, record_calls, Totals, run_usage, chat_usage, usage_by_role
src/phil/agents/pricing.py        create: PriceBook
src/phil/agents/collector.py      create: ModelCall, UsageCollector (langchain callback)
src/phil/agents/invoke.py         modify: collector + cost resolution + calls rows + chat_id + retries
src/phil/agents/retry.py          modify: timeouts / 200-with-error transient; retries count
src/phil/agents/factory.py        modify: build chat-model objects with timeouts
src/phil/agents/fake.py           modify: fakes accept an optional config
src/phil/config.py                modify: [run] model_timeout_s, warn_at
src/phil/run/engine.py            modify: budget warning event; budget from run_usage
src/phil/run/state.py             modify: budget_warned key; summary Usage section + issue cleanup
src/phil/chat/watcher.py          modify: budget_warning event; cost in run_progress
src/phil/cli/attach.py            modify: render budget_warning
src/phil/ui/runs_view.py          modify: ~$ / $? markers
src/phil/ui/show_view.py          create: render_show, show_refs
src/phil/cli/main.py              modify: `phil show <run> [n]`
src/phil/chat/state.py, controller.py, ui/toolbar.py   modify: chat cost, /show, /more, /park, chat_id in ctx
docs/…, tests/live/…              Task 10
```

---

### Task 1: Telemetry schema and totals

**Files:** Modify `src/phil/store/db.py`, `src/phil/store/telemetry.py`; Test `tests/store/test_telemetry.py` (append), `tests/store/test_db.py` (append).

**Interfaces — Produces:**
- Migrations (append to `MIGRATIONS`, one statement per entry or a script split by `;`):
  - `ALTER TABLE telemetry ADD COLUMN chat_id TEXT`
  - `ALTER TABLE telemetry ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0`
  - `ALTER TABLE telemetry ADD COLUMN tool_calls TEXT NOT NULL DEFAULT '{}'`
  - `ALTER TABLE telemetry ADD COLUMN retries INTEGER NOT NULL DEFAULT 0`
  - `ALTER TABLE telemetry ADD COLUMN cost_source TEXT NOT NULL DEFAULT 'reported'`
  - `CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY AUTOINCREMENT, telemetry_id INTEGER NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL, cost_source TEXT NOT NULL, created_at TEXT NOT NULL)`
  (Check how `_migrate` applies entries — each `MIGRATIONS` element is one version; put each `ALTER` in its own element or one element whose statements `_statements` splits.)
- `CostSource = Literal["reported", "estimated", "unknown"]`; `weakest(sources) -> CostSource` (`unknown` > `estimated` > `reported`; empty → `reported`).
- `TelemetryRow` gains `chat_id: str | None = None`, `model_calls: int = 0`, `tool_calls: dict[str, int] = {}`, `retries: int = 0`, `cost_source: CostSource = "reported"`. `record()` stores `tool_calls` as JSON and **returns the new row id**.
- `CallRow(model: str, input_tokens: int, output_tokens: int, cost_usd: float, cost_source: CostSource)`; `record_calls(conn, telemetry_id, calls: Sequence[CallRow]) -> None`.
- `Totals(tokens: int, cost_usd: float, cost_source: CostSource)` (frozen dataclass); `run_usage(conn, run_id) -> Totals`; `chat_usage(conn, chat_id) -> Totals` = chat-layer telemetry with that `chat_id` + all telemetry of runs whose `runs.chat_id` is it. `run_totals` stays (returns `(tokens, cost)`) for existing callers.
- `UsageLine` gains `model_calls: int`, `tool_calls: dict[str, int]`, `retries: int`, `cost_source: CostSource`; `usage_by_role` aggregates them (tool_calls summed per name in Python).
- `format_cost(cost: float, source: CostSource) -> str` in `phil.store.telemetry` (or `phil.ui` — keep it next to Totals): `$0.42`, `~$0.42` for estimated, `$0.42?` for unknown-with-partial or `$?` when cost is 0 and unknown.

- [ ] **Step 1: Failing tests** — in `tests/store/test_telemetry.py` (use the existing `conn` fixture and row helper; read the file first):

```python
def test_record_returns_the_id_and_stores_the_new_fields(conn):
    telemetry_id = record(conn, row(run_id="r-1", tool_calls={"read_file": 2}, model_calls=3, retries=1, cost_source="estimated", chat_id="c-1"))
    stored = conn.execute("SELECT * FROM telemetry WHERE id = ?", (telemetry_id,)).fetchone()
    assert json.loads(stored["tool_calls"]) == {"read_file": 2}
    assert (stored["model_calls"], stored["retries"], stored["cost_source"], stored["chat_id"]) == (3, 1, "estimated", "c-1")


def test_record_calls(conn):
    telemetry_id = record(conn, row(run_id="r-1"))
    record_calls(conn, telemetry_id, [CallRow("openrouter:x", 10, 5, 0.01, "reported"), CallRow("openrouter:x", 20, 5, 0.0, "unknown")])
    assert conn.execute("SELECT COUNT(*) FROM calls WHERE telemetry_id = ?", (telemetry_id,)).fetchone()[0] == 2


def test_run_usage_takes_the_weakest_cost_source(conn):
    record(conn, row(run_id="r-1", input_tokens=100, output_tokens=10, cost_usd=0.10, cost_source="reported"))
    record(conn, row(run_id="r-1", input_tokens=50, output_tokens=5, cost_usd=0.05, cost_source="estimated"))
    assert run_usage(conn, "r-1") == Totals(165, 0.15, "estimated")
    assert run_usage(conn, "r-none") == Totals(0, 0.0, "reported")


def test_chat_usage_includes_the_chats_runs(conn):
    create_run(conn, run_id="r-1", keyword="CALC", base_sha="a", worktree=Path("/wt"), tasks_total=1, chat_id="c-1")
    record(conn, row(run_id=None, layer="chat", chat_id="c-1", input_tokens=10, output_tokens=0, cost_usd=0.01))
    record(conn, row(run_id="r-1", input_tokens=20, output_tokens=0, cost_usd=0.02))
    record(conn, row(run_id=None, layer="chat", chat_id="c-2", input_tokens=99, output_tokens=0, cost_usd=0.99))
    assert chat_usage(conn, "c-1") == Totals(30, 0.03, "reported")


def test_usage_by_role_aggregates_tools_and_retries(conn):
    record(conn, row(run_id="r-1", role="implementer", tool_calls={"run_shell": 2}, model_calls=2, retries=1))
    record(conn, row(run_id="r-1", role="implementer", tool_calls={"run_shell": 1, "read_file": 1}, model_calls=1))
    [line] = usage_by_role(conn, "r-1")
    assert line.tool_calls == {"run_shell": 3, "read_file": 1} and line.model_calls == 3 and line.retries == 1


def test_weakest_and_format_cost():
    assert weakest(["reported", "estimated"]) == "estimated"
    assert weakest(["estimated", "unknown"]) == "unknown"
    assert weakest([]) == "reported"
    assert format_cost(0.4212, "reported") == "$0.42"
    assert format_cost(0.4212, "estimated") == "~$0.42"
    assert format_cost(0.0, "unknown") == "$?"
    assert format_cost(0.4212, "unknown") == "$0.42?"
```

(Adapt the `row(...)` helper to accept these keyword overrides with defaults matching the existing tests; import `json`, `Path`, `create_run`.) Add a migration test asserting the new telemetry columns and the `calls` table exist on a fresh db.

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** per the interfaces. SQL for the weakest source: `MAX(CASE cost_source WHEN 'unknown' THEN 2 WHEN 'estimated' THEN 1 ELSE 0 END)` mapped back. `chat_usage`: `WHERE (layer = 'chat' AND chat_id = ?) OR run_id IN (SELECT run_id FROM runs WHERE chat_id = ?)`.
- [ ] **Step 4: Run tests** — `tests/store`, then the full suite.
- [ ] **Step 5: Commit** — `Record tool calls, retries, model calls and cost sources in telemetry`.

---

### Task 2: The price book

**Files:** Create `src/phil/agents/pricing.py`, `tests/agents/test_pricing.py`, `tests/agents/fixtures/openrouter-models.json` (a trimmed copy with 2–3 models).

**Interfaces — Produces:**

```python
MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_MAX_AGE_S = 24 * 3600

@dataclass(frozen=True)
class Price:
    prompt: float       # USD per token
    completion: float

class PriceBook:
    def __init__(self, cache_path: Path, *, fetch: Callable[[], dict] | None = None,
                 clock: Callable[[], float] = time.time, max_age_s: float = CACHE_MAX_AGE_S) -> None
    def price(self, model: str) -> Price | None      # model is Phil's "provider:id" string; only "openrouter:" is priced
    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> float | None

def default_price_book() -> PriceBook   # cache under phil_home() / "cache" / "openrouter-models.json"; fetch via urllib (timeout 10 s)
```

Behaviour: lazy-load on the first `price()`; if the cache file exists and is younger than `max_age_s`, use it; otherwise call `fetch()` (default: `urllib.request.urlopen(MODELS_URL, timeout=10)` → JSON), write the cache atomically, use it; on fetch failure use a stale cache if present, else no prices. Prices come from `data[i]["pricing"]["prompt"|"completion"]` (strings, USD per token) keyed by `data[i]["id"]`. Never raise from `price()`/`estimate()`. Load once per PriceBook (thread-safe with a lock — chat jobs run on threads).

- [ ] **Step 1: Failing tests** (`tests/agents/test_pricing.py`):

```python
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "openrouter-models.json").read_text())


def book(tmp_path, fetch=lambda: FIXTURE, now=1_000_000.0):
    return PriceBook(tmp_path / "models.json", fetch=fetch, clock=lambda: now)


def test_prices_openrouter_models(tmp_path):
    b = book(tmp_path)
    price = b.price("openrouter:openai/gpt-6-sol")
    assert price == Price(prompt=0.000002, completion=0.00001)
    assert b.estimate("openrouter:openai/gpt-6-sol", 1000, 100) == pytest.approx(0.003)
    assert b.price("openrouter:nope/unknown") is None
    assert b.price("anthropic:claude-sonnet-5") is None


def test_uses_a_fresh_cache_without_fetching(tmp_path):
    book(tmp_path).price("openrouter:openai/gpt-6-sol")  # writes the cache
    def boom():
        raise AssertionError("fetched")
    assert book(tmp_path, fetch=boom, now=1_000_000.0 + 60).price("openrouter:openai/gpt-6-sol") is not None


def test_refreshes_a_stale_cache_and_falls_back_on_failure(tmp_path):
    book(tmp_path).price("openrouter:openai/gpt-6-sol")
    calls = []
    def failing():
        calls.append(1)
        raise OSError("offline")
    later = book(tmp_path, fetch=failing, now=1_000_000.0 + 2 * 86400)
    assert later.price("openrouter:openai/gpt-6-sol") is not None  # stale cache used
    assert calls == [1]


def test_no_cache_and_no_network_means_no_prices(tmp_path):
    def failing():
        raise OSError("offline")
    assert book(tmp_path, fetch=failing).estimate("openrouter:openai/gpt-6-sol", 10, 10) is None
```

The fixture holds `{"data": [{"id": "openai/gpt-6-sol", "pricing": {"prompt": "0.000002", "completion": "0.00001"}}, {"id": "openai/gpt-6-luna", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}}]}`.

- [ ] **Step 2–5:** verify failure, implement, run tests + full suite, commit `Add a cached price book for estimating model costs`.

---

### Task 3: The usage collector

**Files:** Create `src/phil/agents/collector.py`, `tests/agents/test_collector.py`.

**Interfaces — Produces:**

```python
@dataclass(frozen=True)
class ModelCall:
    model: str | None          # model name from the response/serialized llm, if known
    input_tokens: int
    output_tokens: int
    reported_cost: float | None

class UsageCollector(BaseCallbackHandler):
    def __init__(self, *, ignore_tools: Iterable[str] = ()) -> None
    calls: list[ModelCall]              # one per chat-model call (top-level and nested)
    tool_calls: dict[str, int]          # tool name → starts, excluding ignore_tools
    # hooks: on_llm_end(response: LLMResult, *, run_id, parent_run_id, **kw), on_tool_start(serialized, input_str, *, run_id, **kw)
```

`on_llm_end`: for each generation with a `message`, read `message.usage_metadata` (`input_tokens`, `output_tokens`) and `message.response_metadata.get("cost")`; also fall back to `response.llm_output.get("token_usage")` when a generation has no usage metadata; model name from `response_metadata.get("model_name") or response_metadata.get("model")`. `on_tool_start`: tool name from `serialized.get("name")` or `kwargs.get("name")`; skip names in `ignore_tools`. Thread-safe append (a lock) — nested runs may call back from other threads. `invoke_agent` passes `ignore_tools={spec.out_contract.__name__}` (ToolStrategy names its tool after the schema — verify against the installed langchain and adjust).

- [ ] **Step 1: Failing tests** — drive the hooks directly with real `langchain_core` objects (`LLMResult`, `ChatGeneration`, `AIMessage(content="", usage_metadata={...}, response_metadata={"cost": 0.01, "model_name": "openai/gpt-6-sol"})`): two top-level calls and one with a `parent_run_id` (nested) → 3 `calls`; a call without `cost` → `reported_cost is None`; tool starts for `read_file` ×2, `run_shell` ×1 and the contract tool `PlanCritique` (ignored) → `{"read_file": 2, "run_shell": 1}`. Also an end-to-end test with a real `create_agent` over a fake chat model (`langchain_core.language_models.fake_chat_models.GenericFakeChatModel` or similar that supports tool calls — if tool calling fakes are impractical, keep to a single model call) invoked with `config={"callbacks": [collector]}` proving propagation (collector sees the call). Keep it offline.
- [ ] **Step 2–5:** verify failure, implement, tests + full suite, commit `Add a usage collector that sees every model and tool call`.

---

### Task 4: Wire accounting into `invoke_agent`

**Files:** Modify `src/phil/agents/invoke.py`, `src/phil/agents/retry.py` (retry count only), `src/phil/agents/fake.py`; Test `tests/agents/test_invoke.py` (append), `tests/agents/test_retry.py` (append).

**Interfaces — Produces:**
- `AgentContext` gains `chat_id: str | None = None` and `prices: PriceBook | None = None` (lazy default: `default_price_book()` created on first use inside `invoke_agent`; tests pass a fixture book).
- `call_with_retry(agent, payload, *, sleep, attempts=3, base_delay=1.0, config=None) -> tuple[dict, int]` — passes `config` to `agent.invoke(payload, config=config)` when not None, returns `(result, retries)`; update its callers and tests.
- Fakes: `FakeAgent.invoke(self, payload, config=None)` and `_ScriptedAgent.invoke(self, payload, config=None)`.
- In `invoke_agent`, per attempt: create `UsageCollector(ignore_tools={spec.out_contract.__name__})` (lazy import), invoke with `config={"callbacks": [collector]}`. Resolve each `ModelCall`'s cost: `reported_cost` → `reported`; else `prices.estimate(model, in, out)` → `estimated` (use the call's model name if it matches the configured model family, else the configured model string); else `unknown` with cost 0. If the collector saw no model calls (scripted fakes), fall back to `extract_usage(result["messages"])` exactly as today (cost_source `reported`, model_calls 0, and no `calls` rows). Telemetry row: summed tokens/cost, `model_calls=len(calls)`, `tool_calls=collector.tool_calls`, `retries`, `cost_source=weakest(...)`, `chat_id=ctx.chat_id`; then `record_calls`. Error rows (`_record_error`) keep working (retries included; collector calls recorded if any happened before the error).

- [ ] **Step 1: Failing tests** — a fake agent class in the test whose `invoke(payload, config)` fires the collector hooks from `config["callbacks"][0]` (simulating nested model calls and tool starts) and returns a structured response; assert the telemetry row (model_calls, tool_calls, cost_source, summed tokens), `calls` rows, `chat_id` passthrough, estimated cost via a fixture `PriceBook`, unknown cost when unpriced, the scripted-factory fallback unchanged, and `retries` counted when the first `invoke` raises a transient error.
- [ ] **Step 2–5:** verify failure, implement, tests + full suite (`tests/agents tests/run tests/chat`), commit `Count every model call, tool call and retry, and cost them`.

---

### Task 5: Timeouts and transient responses

**Files:** Modify `src/phil/config.py`, `src/phil/agents/factory.py`, `src/phil/agents/retry.py`, `src/phil/agents/invoke.py` (pass the timeout into the factory); Test `tests/test_config.py`, `tests/agents/test_factory.py`, `tests/agents/test_retry.py`.

**Interfaces — Produces:**
- `RunConfig.model_timeout_s: int = 180` (>0) and `RunConfig.warn_at: float = 0.8` (0 < warn_at < 1).
- The factory builds the model object: `chat_model(model: str, timeout_s: int) -> BaseChatModel` in `phil.agents.factory` (lazy imports) using `langchain.chat_models.init_chat_model(model, **kwargs)` with provider-specific kwargs — for `openrouter:` → `timeout=timeout_s * 1000` (milliseconds; `ChatOpenRouter.request_timeout` alias `timeout`) and `max_retries=0`; for `openai:`/`anthropic:`/`google_genai:` → `timeout=timeout_s`, `max_retries=0`; unknown providers → no extra kwargs. `build_agent(spec, model, workdir, tools, *, timeout_s=180)` passes the object to `create_agent` / `create_deep_agent`. Keep the `AgentFactory` signature compatible (add `timeout_s` as keyword with a default; scripted factories ignore it). `invoke_agent` passes `ctx.config.run.model_timeout_s`.
- `is_transient` also returns True for: provider SDK timeouts (e.g. `openrouter` SDK / `httpx` timeout classes by name — find the exact exception types the installed `openrouter` SDK raises on timeout and on 5xx/429, and match them by module+name like `_is_httpx_transient`), and "200-with-error" responses: an exception carrying a status of 200 (or none) whose error payload code is in `TRANSIENT_STATUS` — inspect how `langchain_openrouter` surfaces an error body on a 200 and match that shape. Non-transient 4xx (e.g. 400 "Provider returned error") stay non-transient.

- [ ] **Step 1: Failing tests** — config defaults and validation; `chat_model("openrouter:openai/gpt-6-luna", 180)` with a dummy `OPENROUTER_API_KEY` env has `request_timeout == 180000` and `max_retries == 0` (inspect attributes; no network); an OpenAI-style model gets `timeout == 180` (skip if the provider package isn't installed); `build_agent` passes a model object (monkeypatch `create_agent` like the existing factory tests); `is_transient` on constructed SDK exceptions (timeout → True, 503 → True, 200-with-error 503 → True, 400 → False).
- [ ] **Step 2–5:** verify failure, implement, tests + full suite, commit `Put a timeout on every model call and retry transient responses`.

---

### Task 6: Budget warnings

**Files:** Modify `src/phil/run/engine.py`, `src/phil/run/state.py` (RunState `budget_warned: bool`), `src/phil/chat/watcher.py`, `src/phil/chat/controller.py`, `src/phil/cli/attach.py`; Tests in `tests/run/test_engine_budget*.py` (find the existing budget tests), `tests/chat/test_watcher.py`, `tests/chat/test_controller_run.py`, `tests/cli/test_attach.py`.

**Interfaces — Produces:**
- The engine's budget check uses `run_usage` (tokens, cost). Where it checks the budget (every node that calls `_budget_escalation`), before escalating: if not `state.get("budget_warned")` and (tokens ≥ warn_at × max_tokens or cost ≥ warn_at × max_cost), append `events.append("budget_warning", tokens=…, cost_usd=…, max_tokens=…, max_cost_usd=…, cost_source=…)` (when `deps.events` exists) and return `{"budget_warned": True}` merged into the node's update (don't lose the node's normal result; the warning never escalates). Only once per run (the flag lives in the checkpointed state). After a `continue` that raises the limits, the warning may fire once more for the new limit — reset `budget_warned` when limits change.
- `RunWatcher` posts `ChatEvent("budget_warning", {...})` for each new `budget_warning` log event (track by event `ts`); the controller prints `r-7f3a has used 80% of its budget ($1.61 of $2.00).` (tokens variant when the token limit is the one crossed: `… 320,000 of 400,000 tokens.`), using `format_cost`. `phil attach`'s `render_event` prints the same line.
- `run_progress` from the watcher adds `tokens`, `cost_usd`, `cost_source` (from `run_usage`) so the chat can show cost (Task 9).

- [ ] **Step 1: Failing tests** — engine: a run crossing 80% emits exactly one `budget_warning` event and completes normally; crossing 100% still escalates as before. Watcher: posts `budget_warning` once per event. Controller: prints the warning line. Attach: renders it.
- [ ] **Step 2–5:** verify failure, implement, tests + full suite, commit `Warn when a run passes 80% of its budget`.

---

### Task 7: Run summary usage and cleanup; `phil runs` markers

**Files:** Modify `src/phil/run/state.py` (`render_summary`, `issues_to_tasks` or wherever open issues accumulate), `src/phil/run/engine.py` (pass usage into the summary), `src/phil/ui/runs_view.py`; Tests `tests/run/test_state.py` (or the summary tests), `tests/test_cli.py` / `tests/cli` for `phil runs`.

**Interfaces — Produces:**
- `clean_note(text: str, *, limit: int = 200) -> str` — collapse whitespace/newlines to single spaces, strip markdown emphasis (`**`, `__`, `*x*`, `_x_`), backticks and leading `#`/list markers, cap to `limit` with `…`.
- `dedupe_issues(issues: list[dict]) -> list[dict]` — key `(task_id, clean_note(note).lower())`; keep the highest severity (`blocker` > `major` > `minor`); stable order of first appearance. Applied wherever open issues are accumulated for the summary (tester notes + review issues) and in `render_summary`.
- `render_summary(..., usage: list[UsageLine] | None = None, totals: Totals | None = None)` appends:

```
## Usage
Total: 203,812 tokens · ~$0.41 (estimated)
- run/implementer: 6 calls (14 model calls) · 150,210 in / 9,120 out · ~$0.33 · tools: run_shell×9, read_file×12
- run/reviewer: 1 call · …
```
  (cost via `format_cost`; `(estimated)` / `(partly unknown)` suffix on the total when not all reported).
- `phil runs` shows cost with `format_cost` from `run_usage` (so `~$0.41`, `$?`).

- [ ] **Step 1: Failing tests** — `clean_note` cases (newlines, `**bold**`, backticks, cap); `dedupe_issues` keeps the highest severity and first order; `render_summary` with usage lines renders the Usage section; issue lines are one-line; `phil runs` shows `~$` for an estimated run.
- [ ] **Step 2–5:** verify failure, implement, tests + full suite, commit `Add usage to run summaries, clean up open issues, mark estimated costs`.

---

### Task 8: `phil show`

**Files:** Create `src/phil/ui/show_view.py`, `tests/ui/test_show_view.py`, `tests/cli/test_show_command.py`; Modify `src/phil/cli/main.py`.

**Interfaces — Produces:**
- `show_refs(paths: ProjectPaths, run_id: str) -> list[Ref]` — numbered detail refs, in this order, each only if the file exists: `summary` (runs/<id>/summary.md), `worker log` (logs/worker.log), test logs (logs/*.log other than worker.log, newest first, up to 5), reviewer/tester outputs (outputs/*review*/*tester* newest first, up to 5), packets (packets/*.json newest first, up to 5). Labels are short (`test log verify-CALC-001-2`).
- `render_show(console, conn, paths, run_id) -> list[Ref]` — prints: header `Run r-7f3a · CALC · completed · base..branch · 2/2 tasks`, tasks with status (from the stored plan + row), the usage table (layer, role, calls, model calls, tokens in/out, cost, tools, retries; rich `Table`), open issues (from summary cleanup helpers — read the latest open issues from the checkpoint-free source: parse `summary.md`'s Open issues section, or store them in `runs/<id>/open_issues.json` when the summary is written — prefer the JSON file, written by the engine next to the summary), then the numbered refs; returns the refs.
- `phil show RUN [N]`: unknown run → exit 1; with `N`, print ref N's file contents (text; JSON pretty-printed; cap at 2,000 lines with a note) or `No detail #N for <run>.` exit 1.

- [ ] **Step 1: Failing tests** — build a finished run with `run_worker` + scripted factory (as other CLI tests do), then `phil show <id>` shows the header, usage table, a numbered `summary` ref; `phil show <id> 1` prints the summary text; bad N → exit 1; unknown run → exit 1.
- [ ] **Step 2–5:** verify failure, implement, tests + full suite, commit `Add phil show for a run's usage, issues and details`.

---

### Task 9: Chat — cost on the toolbar, `/show`, `/more`, `/park`

**Files:** Modify `src/phil/chat/state.py`, `src/phil/ui/toolbar.py`, `src/phil/chat/controller.py`; Tests `tests/ui/test_toolbar.py`, `tests/chat/test_controller.py` / `test_controller_run.py`.

**Interfaces — Produces:**
- `ToolbarView.cost: tuple[float, str] | None = None` (`(cost_usd, cost_source)`); `ChatState.set_cost(cost, source)`; `render_toolbar` appends a `format_cost(...)` segment (lowest priority before `/btw`; dropped first on narrow terminals).
- Controller: passes `chat_id=self.session.id` into its `AgentContext` (so job contexts inherit it); refreshes the cost from `chat_usage(conn, chat_id)` on the main thread after each job event and on each `run_progress`/`run_done`.
- `/show`: when the chat has (or last had) a run → `render_show` for it and remember its refs as the "last refs"; else `No run to show yet.`
- `/more <n>`: expands ref `n` from the last refs (from `/show`, a completion notice, or a `/btw` answer's `details`); prints the file like `phil show RUN N`; bad n → `No detail #<n>. Use /show to list them.`
- Completion notices list numbered refs (`show_refs` for the run, first 3) and set the last refs; `/btw` answers' `details` refs become the last refs (numbered in the printed Brief).
- `/park <note>` (empty → `Usage: /park <note>`): `park(conn, raised_by="user", note=note, why_not_now="parked from chat", source=Ref(label=f"chat {chat_id}", path=str(session.dir)), run_id=<chat's run or None>)` on the main thread; print `Parked P-012.`; the toolbar/header parked count updates if shown. HELP lists `/show`, `/more <n>`, `/park <note>`.

- [ ] **Step 1: Failing tests** — toolbar cost segment and priority; controller: cost appears in `state.view().cost` after a scripted plan (telemetry recorded with chat_id); `/park` creates a parked row linked to the chat's run; `/show` renders after a run completes; `/more 1` prints the summary; bad `/more` message.
- [ ] **Step 2–5:** verify failure, implement, tests + full suite, commit `Show chat cost and add /show, /more and /park`.

---

### Task 10: Docs and the live test

**Files:** Modify `docs/superpowers/specs/2026-09-23-phil-v1-design.md` (§11 observability: point to the 4c design), `README.md` (`phil show`, `/show`, `/more`, `/park`, `[run] model_timeout_s`, `[run] warn_at`, cost markers), `docs/superpowers/plans/2026-09-28-phil-04b-followups.md` / `2026-09-24-phil-04a-followups.md` (mark 4c items done; keep the lean architect deferred), `tests/live/test_invoke_live.py` (assert the telemetry row has `model_calls >= 1` and `cost_source in {"reported", "estimated"}`, and that a `calls` row exists).

- [ ] **Step 1:** make the edits (accurate to the code).
- [ ] **Step 2:** full suite → PASS (live test deselected by default).
- [ ] **Step 3: Commit** — `Document observability and extend the live test`.

---

## Spec coverage

| Spec (4c design) | Task |
|---|---|
| §3.1 collector sees every model call incl. sub-agents; tool counts | 3, 4 |
| §3.1 cost resolution + PriceBook (cached, offline-safe) | 2, 4 |
| §3.1 storage (calls table, telemetry fields), chat cost | 1, 4, 9 |
| §3.1 budget guard on accurate totals | 6 |
| §3.2 `phil runs`, summary Usage | 7 |
| §3.2 `phil show` | 8 |
| §3.2 chat toolbar cost, `/show`, `/more` | 9 |
| §3.2 budget warning (engine → watcher → chat, attach) | 6 |
| §3.3 timeouts (per-provider units, SDK retries off) and transient 200-with-error | 5 |
| §3.4 `/park` | 9 |
| §3.5 summary cleanup (dedupe, one-line notes) | 7 |
| §4 testing incl. live | every task, 10 |
