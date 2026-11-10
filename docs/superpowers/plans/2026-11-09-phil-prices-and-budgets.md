# Prices and Budgets on Every Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Direct Anthropic, OpenAI and Google calls get a dollar cost from OpenRouter's public price list (cache and long-context prices included), so the cost budgets work on them; a model Phil can't price gets one notice; direct Anthropic caches prompts in the light harness.

**Architecture:** `phil.agents.pricing` learns cache prices, long-context tiers and how to match a direct model name to an OpenRouter id (`book_ids`, `PriceBook.find`, `Price.cost`). `invoke._estimate` tries configured prices before the book and feeds cache read/write token counts into `Price.cost`. A new `phil.agents.unpriced` finds configured models with no price and words the notice; the run worker and the chat show it at run and goal start; `phil models check` shows each model's price. The light harness adds `AnthropicPromptCachingMiddleware` for the `anthropic` kind.

**Tech Stack:** Python 3.14, LangChain 1.x (`langchain_anthropic.middleware.AnthropicPromptCachingMiddleware`), deepagents, SQLite, pytest (xdist).

**Spec:** `docs/superpowers/specs/2026-11-09-phil-prices-and-budgets-design.md`

## Global Constraints

- Price order for a call with no reported cost: configured (`input_per_mtok` and `output_per_mtok` both set) → price book → unknown (spec §4.1).
- The book prices providers of kind `openrouter`, `anthropic`, `openai`, `google` only; kind `openai` with a `base_url` other than host `api.openai.com`, and kind `anthropic`/`google` with any `base_url`, are never looked up (§4.2).
- Matching rules, first match wins: as is; a `-` between two digits becomes `.`; a trailing `-YYYYMMDD` dropped, then the dash rule. Ids ending in `:batch` never match (§4.2).
- Estimate: `uncached = input − cache_read − cache_write` (never below 0); a missing cache price falls back to the prompt price; a tier applies when `input_tokens >= min_prompt_tokens`, highest threshold wins, its missing fields fall back to the base entry's (§4.5).
- New telemetry column `cache_write_tokens INTEGER NOT NULL DEFAULT 0` (§4.4).
- Notice text, run: ``No price for `<model>` (<roles>), so the $<max_cost_usd:.2f> run budget can't count it. The run still stops at <max_tokens:,> tokens. To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.<name>]`.`` Planning names `chat.max_cost_usd` and has no token sentence. Nothing is blocked (§4.6).
- Caching middleware: `AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")`, light harness, provider kind `anthropic` only; lean unchanged; deep unchanged (§4.7).
- Tests must never touch the network: `tests/conftest.py`'s `no_price_fetch` guard fails any test that reaches `phil.agents.pricing._default_fetch`. A test that now needs prices gets a `PriceBook` with a fixture `fetch`; never weaken the guard.
- Repo files are edited with Edit/Write only. Commits use `.git/phil-commit-msg.txt` and `git commit -F`, ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage only the files the task names.
- Test command: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q <paths>` (one worker test runs bare `pytest`, so `.venv/bin` must be on PATH).

## Review Focus

1. **Anthropic's cache-write breakdown.** When the API reports `cache_creation.ephemeral_5m_input_tokens`, LangChain sets `input_token_details["cache_creation"]` to 0 and puts the count under `ephemeral_5m_input_tokens`; reading only `cache_creation` would record 0 writes and undercharge. Task 2 pins it.
2. **`input_tokens` includes cached tokens.** `langchain_anthropic` adds cache reads and writes back into `input_tokens`; if that ever stopped, `uncached` would go to 0 and every call would be underpriced. Task 2 pins it against `_create_usage_metadata`.
3. **A proxy set as `[providers.anthropic] base_url = …`.** It must count as unpriced (no book lookup) and the notice must name `[providers.anthropic]`. Task 4 pins it.
4. **The goal-start notice must never break a goal.** A price-list failure or a config error in it must show nothing, not a "job failed" line. Task 5 pins it.
5. **An older cached price list** (entries with only `prompt`/`completion`) must still price calls, with cache tokens at the prompt price. Task 1 pins it.

---

### Task 1: Prices with cache and long-context tiers; matching a direct model

**Files:**
- Modify: `src/phil/agents/pricing.py`
- Test: `tests/agents/test_pricing.py`

**Interfaces:**
- Produces:
  - `Tier(min_prompt_tokens: int, prompt: float, completion: float, cache_read: float | None = None, cache_write: float | None = None)` (frozen dataclass)
  - `Price(prompt: float, completion: float, cache_read: float | None = None, cache_write: float | None = None, tiers: tuple[Tier, ...] = ())` with `Price.cost(input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float`
  - `book_ids(kind: str, base_url: str | None, name: str) -> list[str]`
  - `PriceBook.find(ids: Sequence[str]) -> Price | None` (ids without `openrouter:`; `[]` returns `None` without loading)
  - `PriceBook.price(model)` and `PriceBook.estimate(model, input_tokens, output_tokens)` keep their behaviour for `openrouter:` models.

- [ ] **Step 1: Write the failing tests** (append to `tests/agents/test_pricing.py`; add `from phil.agents.pricing import Tier, book_ids` to the imports)

```python
DIRECT = {
    "data": [
        {
            "id": "anthropic/claude-sonnet-5.5",
            "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000001",
                        "input_cache_write": "0.0000025", "input_cache_write_1h": "0.000004"},
        },
        {"id": "anthropic/claude-sonnet-5.5:batch", "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
        {
            "id": "openai/gpt-6.1-terra",
            "pricing": {
                "prompt": "0.000001", "completion": "0.000005", "input_cache_read": "0.00000005",
                "overrides": [
                    {"min_prompt_tokens": 272000, "prompt": "0.000002", "completion": "0.0000075"},
                    {"min_prompt_tokens": 500000, "prompt": "0.000003", "completion": "0.00001",
                     "input_cache_read": "0.0000002"},
                ],
            },
        },
        {"id": "anthropic/claude-haiku-4.5", "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
        {"id": "google/gemini-3.8-flash", "pricing": {"prompt": "0.000000825", "completion": "0.000004125"}},
    ]
}


def direct_book(tmp_path):
    return book(tmp_path, fetch=lambda: DIRECT)


def test_cache_and_tier_prices_are_parsed(tmp_path):
    sonnet = direct_book(tmp_path).find(["anthropic/claude-sonnet-5.5"])
    assert sonnet == Price(prompt=0.000002, completion=0.00001, cache_read=0.0000001, cache_write=0.0000025)
    terra = direct_book(tmp_path).find(["openai/gpt-6.1-terra"])
    assert terra.tiers == (
        Tier(272000, prompt=0.000002, completion=0.0000075, cache_read=0.00000005, cache_write=None),
        Tier(500000, prompt=0.000003, completion=0.00001, cache_read=0.0000002, cache_write=None),
    )


def test_cost_charges_cache_reads_and_writes_at_their_prices():
    price = Price(prompt=0.000002, completion=0.00001, cache_read=0.0000001, cache_write=0.0000025)
    cost = price.cost(1000, 100, cache_read_tokens=600, cache_write_tokens=200)
    assert cost == pytest.approx(200 * 0.000002 + 600 * 0.0000001 + 200 * 0.0000025 + 100 * 0.00001)


def test_a_missing_cache_price_falls_back_to_the_prompt_price():
    # An older cached list has only prompt and completion prices.
    price = Price(prompt=0.000002, completion=0.00001)
    assert price.cost(1000, 100, cache_read_tokens=600, cache_write_tokens=200) == pytest.approx(
        1000 * 0.000002 + 100 * 0.00001
    )


def test_uncached_tokens_never_go_below_zero():
    price = Price(prompt=0.000002, completion=0.00001, cache_read=0.0000001)
    assert price.cost(100, 0, cache_read_tokens=150) == pytest.approx(150 * 0.0000001)


def test_the_highest_tier_the_call_reaches_prices_the_whole_call(tmp_path):
    terra = direct_book(tmp_path).find(["openai/gpt-6.1-terra"])
    assert terra.cost(1000, 10) == pytest.approx(1000 * 0.000001 + 10 * 0.000005)
    assert terra.cost(300_000, 10) == pytest.approx(300_000 * 0.000002 + 10 * 0.0000075)
    assert terra.cost(600_000, 10, cache_read_tokens=100_000) == pytest.approx(
        500_000 * 0.000003 + 100_000 * 0.0000002 + 10 * 0.00001
    )


def test_a_tier_missing_a_cache_price_keeps_the_base_entrys():
    # The 272k override above names no input_cache_read: the base entry's 0.00000005 applies.
    terra_tier = Tier(272000, prompt=0.000002, completion=0.0000075, cache_read=0.00000005)
    price = Price(prompt=0.000001, completion=0.000005, cache_read=0.00000005, tiers=(terra_tier,))
    assert price.cost(300_000, 0, cache_read_tokens=200_000) == pytest.approx(100_000 * 0.000002 + 200_000 * 0.00000005)


@pytest.mark.parametrize(
    ("kind", "name", "expected"),
    [
        ("openai", "gpt-6.1-terra", ["openai/gpt-6.1-terra"]),
        ("anthropic", "claude-sonnet-5-5", ["anthropic/claude-sonnet-5-5", "anthropic/claude-sonnet-5.5"]),
        (
            "anthropic",
            "claude-haiku-4-5-20251001",
            ["anthropic/claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5.20251001", "anthropic/claude-haiku-4.5"],
        ),
        ("google", "gemini-3.8-flash", ["google/gemini-3.8-flash"]),
        ("openrouter", "anthropic/claude-sonnet-5.5", ["anthropic/claude-sonnet-5.5"]),
    ],
)
def test_book_ids_most_exact_first(kind, name, expected):
    assert book_ids(kind, None, name) == expected


def test_direct_models_are_matched_by_their_rules(tmp_path):
    b = direct_book(tmp_path)
    assert b.find(book_ids("anthropic", None, "claude-sonnet-5-5")).prompt == 0.000002
    assert b.find(book_ids("anthropic", None, "claude-haiku-4-5-20251001")).prompt == 0.000001
    assert b.find(book_ids("google", None, "gemini-3.8-flash")).prompt == 0.000000825


def test_batch_entries_are_never_matched(tmp_path):
    assert book_ids("anthropic", None, "claude-sonnet-5-5:batch") == []
    assert direct_book(tmp_path).find(["anthropic/claude-sonnet-5.5"]).prompt == 0.000002  # not the batch price


@pytest.mark.parametrize(
    ("kind", "base_url"),
    [
        ("openai", "http://localhost:11434/v1"),
        ("openai", "https://llm.example.com/v1"),
        ("anthropic", "https://proxy.example.com"),
        ("google", "https://proxy.example.com"),
        ("systemone", None),
    ],
)
def test_endpoints_the_list_doesnt_price_are_never_looked_up(kind, base_url):
    assert book_ids(kind, base_url, "gpt-6.1-terra") == []


def test_openai_itself_with_an_explicit_base_url_is_looked_up():
    assert book_ids("openai", "https://api.openai.com/v1", "gpt-6.1-terra") == ["openai/gpt-6.1-terra"]


def test_find_with_no_ids_never_loads_the_list(tmp_path):
    def boom():
        raise AssertionError("fetched")

    assert book(tmp_path, fetch=boom).find([]) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_pricing.py`
Expected: collection error `ImportError: cannot import name 'Tier' from 'phil.agents.pricing'`.

- [ ] **Step 3: Implement** in `src/phil/agents/pricing.py`

Add imports: `import re`, `from collections.abc import Callable, Sequence`, `from dataclasses import dataclass, replace`, `from urllib.parse import urlsplit`.

Replace the `Price` dataclass with:

```python
@dataclass(frozen=True)
class Tier:
    """A long-context price: it applies to a call whose input reaches `min_prompt_tokens`."""

    min_prompt_tokens: int
    prompt: float
    completion: float
    cache_read: float | None = None
    cache_write: float | None = None


@dataclass(frozen=True)
class Price:
    prompt: float  # USD per token
    completion: float
    cache_read: float | None = None  # None: the list has no cache price; the prompt price is used
    cache_write: float | None = None
    tiers: tuple[Tier, ...] = ()  # ascending by min_prompt_tokens

    def cost(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
        """One call's cost (spec 2026-11-09 §4.5). Cached tokens are part of `input_tokens`."""
        rates: Price | Tier = self
        for tier in self.tiers:
            if input_tokens >= tier.min_prompt_tokens:
                rates = tier  # ascending, so the last one reached is the highest threshold
        read = rates.prompt if rates.cache_read is None else rates.cache_read
        write = rates.prompt if rates.cache_write is None else rates.cache_write
        uncached = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
        return (
            uncached * rates.prompt
            + cache_read_tokens * read
            + cache_write_tokens * write
            + output_tokens * rates.completion
        )


# The vendors whose own APIs the list prices, under `<kind>/<model>` ids.
DIRECT_KINDS = ("anthropic", "openai", "google")
_DATE_SUFFIX = re.compile(r"-\d{8}$")
_DIGIT_DASH = re.compile(r"(?<=\d)-(?=\d)")


def _vendor_endpoint(kind: str, base_url: str | None) -> bool:
    """The vendor's own API: no base_url, or OpenAI's own host. Anything else (a proxy, a local
    server) may charge anything, so the list's price would be a guess."""
    if base_url is None:
        return True
    return kind == "openai" and urlsplit(base_url).hostname == "api.openai.com"


def book_ids(kind: str, base_url: str | None, name: str) -> list[str]:
    """The OpenRouter ids to price a `kind` provider's model `name` by, most exact first (spec
    2026-11-09 §4.2); [] when the list doesn't price that provider."""
    if kind == "openrouter":
        return [name]
    if kind not in DIRECT_KINDS or not _vendor_endpoint(kind, base_url) or name.endswith(":batch"):
        return []
    ids: list[str] = []
    for candidate in (name, _DIGIT_DASH.sub(".", name), _DIGIT_DASH.sub(".", _DATE_SUFFIX.sub("", name))):
        model_id = f"{kind}/{candidate}"
        if model_id not in ids:
            ids.append(model_id)
    return ids


def _rate(pricing: dict, key: str) -> float | None:
    """A non-negative USD-per-token price, or None when missing, malformed or a router's "-1"."""
    if key not in pricing:
        return None
    try:
        value = float(pricing[key])
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _tiers(pricing: dict, base: Price) -> tuple[Tier, ...]:
    tiers: list[Tier] = []
    for override in pricing.get("overrides") or []:
        if not isinstance(override, dict):
            continue
        try:
            threshold = int(override["min_prompt_tokens"])
        except (KeyError, TypeError, ValueError):
            continue

        def rate(key: str, fallback: float | None) -> float | None:
            value = _rate(override, key)
            return fallback if value is None else value

        tiers.append(
            Tier(
                threshold,
                prompt=rate("prompt", base.prompt),
                completion=rate("completion", base.completion),
                cache_read=rate("input_cache_read", base.cache_read),
                cache_write=rate("input_cache_write", base.cache_write),
            )
        )
    return tuple(sorted(tiers, key=lambda tier: tier.min_prompt_tokens))
```

(`rate` returns `float | None`; for `prompt`/`completion` the fallback is a float, so the `Tier` gets floats there.)

Replace `PriceBook._parse` with:

```python
    def _parse(self, data: dict) -> dict[str, Price]:
        prices: dict[str, Price] = {}
        for entry in data.get("data", []):
            model_id = entry.get("id")
            pricing = entry.get("pricing") or {}
            if not model_id or not isinstance(pricing, dict):
                continue
            prompt, completion = _rate(pricing, "prompt"), _rate(pricing, "completion")
            if prompt is None or completion is None:
                continue  # missing, malformed, or OpenRouter's "-1" sentinel for router models (e.g. openrouter/auto)
            base = Price(prompt, completion, _rate(pricing, "input_cache_read"), _rate(pricing, "input_cache_write"))
            prices[model_id] = replace(base, tiers=_tiers(pricing, base))
        return prices
```

Replace `price` and `estimate`, and add `find`:

```python
    def find(self, ids: Sequence[str]) -> Price | None:
        """The price of the first of `ids` (OpenRouter ids, no `openrouter:` prefix) the list has;
        None when it has none of them or is unavailable. No ids: None, without loading the list."""
        if not ids:
            return None
        try:
            with self._lock:
                self._load()
            for model_id in ids:
                price = self._prices.get(model_id)
                if price is not None:
                    return price
            return None
        except Exception:
            return None

    def price(self, model: str) -> Price | None:
        if not model.startswith("openrouter:"):
            return None
        return self.find([model.removeprefix("openrouter:")])

    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        try:
            price = self.price(model)
            return None if price is None else price.cost(input_tokens, output_tokens)
        except Exception:
            return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_pricing.py tests/setup`
Expected: all pass (the existing `Price(prompt=…, completion=…)` equality still holds: the fixture has no cache fields).

- [ ] **Step 5: Commit**

Message: `Prices: cache and long-context prices, and direct models matched to the list`. Stage `src/phil/agents/pricing.py tests/agents/test_pricing.py`.

---

### Task 2: Cache-write token counts

**Files:**
- Modify: `src/phil/agents/collector.py:11-22,112-123`
- Modify: `src/phil/store/db.py:90` (append to `MIGRATIONS`)
- Modify: `src/phil/store/telemetry.py:67` (`TelemetryRow`)
- Modify: `src/phil/agents/invoke.py:97-105,175-180,270,475` (`_Usage`, `_usage`, both `TelemetryRow(...)` sites)
- Test: `tests/agents/test_prompt_cache.py`, `tests/store/test_db.py`

**Interfaces:**
- Produces: `ModelCall.cache_write_tokens: int = 0`; `_Usage.cache_write_tokens: int = 0`; `TelemetryRow.cache_write_tokens: int = 0`; telemetry column `cache_write_tokens`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_prompt_cache.py`:

```python
def _generation_with(details: dict, input_tokens: int = 1_000) -> ChatGeneration:
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens, "output_tokens": 5, "total_tokens": input_tokens + 5,
            "input_token_details": details,
        },
    )
    return ChatGeneration(message=message)


def _one_call(details: dict):
    collector = UsageCollector()
    collector.on_llm_end(LLMResult(generations=[[_generation_with(details)]]), run_id=uuid.uuid4())
    [call] = collector.calls
    return call


def test_the_collector_records_cache_writes():
    call = _one_call({"cache_read": 600, "cache_creation": 200})
    assert (call.cache_read_tokens, call.cache_write_tokens) == (600, 200)


def test_anthropics_ttl_breakdown_counts_as_cache_writes():
    # With Anthropic's per-TTL breakdown, langchain_anthropic zeroes cache_creation and reports the
    # writes under ephemeral_5m_input_tokens / ephemeral_1h_input_tokens instead.
    call = _one_call({"cache_read": 0, "cache_creation": 0, "ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 50})
    assert call.cache_write_tokens == 350


def test_langchain_anthropic_counts_cached_tokens_inside_input_tokens():
    # Phil's estimate subtracts cache reads and writes from input_tokens: that only works while
    # langchain_anthropic adds them back into its total.
    from anthropic.types import Usage
    from langchain_anthropic.chat_models import _create_usage_metadata

    usage = _create_usage_metadata(
        Usage(input_tokens=200, output_tokens=5, cache_read_input_tokens=600, cache_creation_input_tokens=200)
    )
    assert usage["input_tokens"] == 1_000


def test_cache_writes_are_stored(tmp_path):
    conn = connect(tmp_path / "t.db")
    record(conn, TelemetryRow(
        run_id="r-1", layer="run", node="implement", role="implementer", model="m", attempt=1, packet_tokens=0,
        input_tokens=1_000, output_tokens=10, latency_ms=0, cost_usd=0.1, outcome="ok", cache_write_tokens=200,
    ))
    assert conn.execute("SELECT cache_write_tokens FROM telemetry").fetchone()[0] == 200
```

Append to `tests/store/test_db.py`:

```python
def test_the_cache_write_migration_runs_on_an_existing_database(tmp_path, monkeypatch):
    path = tmp_path / "phil.db"
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS[:-1])  # the database as it was before cache_write_tokens
    connect(path).close()
    monkeypatch.setattr(db, "MIGRATIONS", MIGRATIONS)
    conn = connect(path)
    assert user_version(conn) == len(MIGRATIONS)
    assert "cache_write_tokens" in [row["name"] for row in conn.execute("PRAGMA table_info(telemetry)")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_prompt_cache.py tests/store/test_db.py`
Expected: `AttributeError: 'ModelCall' object has no attribute 'cache_write_tokens'`, a pydantic error for `cache_write_tokens` on `TelemetryRow` (extra fields), and the migration test failing on the missing column. `test_langchain_anthropic_counts_cached_tokens_inside_input_tokens` passes already: it pins a dependency's behaviour (Review Focus 2), so a pass here is expected; if `anthropic.types.Usage` takes different field names in the installed version, adjust the constructor to that version's names, not the assertion.

- [ ] **Step 3: Implement**

`src/phil/agents/collector.py`: add the field and replace `_cache_reads`:

```python
    cache_read_tokens: int = 0  # of input_tokens, how many were read from the provider's prompt cache
    cache_write_tokens: int = 0  # of input_tokens, how many were written to it


def _cache_tokens(message: object) -> tuple[int, int]:
    """(read, written) prompt-cache tokens. With Anthropic's per-TTL breakdown, langchain_anthropic
    reports the writes under `ephemeral_5m/1h_input_tokens` and zeroes `cache_creation`."""
    details = (getattr(message, "usage_metadata", None) or {}).get("input_token_details") or {}
    read = int(details.get("cache_read", 0) or 0)
    by_ttl = int(details.get("ephemeral_5m_input_tokens", 0) or 0) + int(details.get("ephemeral_1h_input_tokens", 0) or 0)
    return read, by_ttl or int(details.get("cache_creation", 0) or 0)
```

In `on_llm_end`, before `new_calls.append(`: `cache_read, cache_write = _cache_tokens(message)`; pass `cache_read_tokens=cache_read, cache_write_tokens=cache_write` to `ModelCall`.

`src/phil/store/db.py`: append to `MIGRATIONS`:

```python
    "ALTER TABLE telemetry ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0",  # spec 2026-11-09 §4.4
```

`src/phil/store/telemetry.py`, in `TelemetryRow` after `cache_read_tokens`:

```python
    cache_write_tokens: int = 0  # of input_tokens, how many were written to the provider's prompt cache
```

`src/phil/agents/invoke.py`: add `cache_write_tokens: int = 0` to `_Usage`; in `_usage`'s return add `cache_write_tokens=sum(model_call.cache_write_tokens for model_call in collector.calls),`; at both `TelemetryRow(` sites add `cache_write_tokens=usage.cache_write_tokens,` after `cache_read_tokens=usage.cache_read_tokens,`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents tests/store`
Expected: all pass.

- [ ] **Step 5: Commit**

Message: `Telemetry: cache writes counted, Anthropic's per-TTL breakdown included`. Stage the five source files and the two test files.

---

### Task 3: Configured prices first, then the book for direct providers, with cache math

**Files:**
- Modify: `src/phil/agents/invoke.py:84-93,107-140,154-162`
- Modify: `docs/guide.md:185-189,448-454`
- Test: `tests/agents/test_invoke.py`

**Interfaces:**
- Consumes: `book_ids`, `PriceBook.find`, `Price.cost` (Task 1); `ModelCall.cache_read_tokens`, `ModelCall.cache_write_tokens` (Task 2).
- Produces: `price_book_for(ctx: AgentContext) -> PriceBook` (public; replaces `_price_book`), `shared_price_book() -> PriceBook` (the process-wide default book, created on first use). Task 4 and Task 5 use both.

- [ ] **Step 1: Write the failing tests** in `tests/agents/test_invoke.py`

Change `_llm_result` to take cache details (CallbackAgent passes `*args`, so a step may add a sixth item):

```python
def _llm_result(input_tokens, output_tokens, cost=None, model_name=None, cache=None):
    ...
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            **({"input_token_details": cache} if cache else {}),
        },
        response_metadata=metadata,
    )
```

Replace `test_openrouter_price_book_wins_over_provider_prices` with:

```python
def test_provider_prices_win_over_the_openrouter_price_book(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig.model_validate(
        {
            "models": TEST_MODELS | {"critic": OPENROUTER_MODEL},
            "providers": {"openrouter": {"input_per_mtok": 1000.0, "output_per_mtok": 1000.0}},
        }
    )
    agent = CallbackAgent([("llm", 1000, 100, None, "openai/gpt-6-sol")], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=no_fetch_price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert (row["cost_source"], row["cost_usd"]) == ("estimated", pytest.approx(1100 / 1e6 * 1000.0))
```

Add:

```python
DIRECT_PRICES = {
    "data": [
        {
            "id": "anthropic/claude-sonnet-5.5",
            "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000001",
                        "input_cache_write": "0.0000025"},
        },
        {"id": "openai/gpt-6.1-terra", "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
    ]
}


def direct_price_book(tmp_path):
    return PriceBook(tmp_path / "direct.json", fetch=lambda: DIRECT_PRICES, clock=lambda: 1_000_000.0)


def test_a_direct_anthropic_call_is_priced_from_the_book_with_its_cache_tokens(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig(models=TEST_MODELS | {"critic": "anthropic:claude-sonnet-5-5"})
    agent = CallbackAgent(
        [("llm", 1000, 100, None, "claude-sonnet-5-5", {"cache_read": 600, "cache_creation": 200})], critique()
    )
    ctx = accounting_context(config, conn, artifacts, agent, prices=direct_price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["cost_source"] == "estimated"
    assert row["cost_usd"] == pytest.approx(200 * 0.000002 + 600 * 0.0000001 + 200 * 0.0000025 + 100 * 0.00001)
    assert (row["cache_read_tokens"], row["cache_write_tokens"]) == (600, 200)


def test_a_direct_openai_call_is_priced_from_the_book(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig(models=TEST_MODELS | {"critic": "openai:gpt-6.1-terra"})
    agent = CallbackAgent([("llm", 1000, 100, None, "gpt-6.1-terra")], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=direct_price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert (row["cost_source"], row["cost_usd"]) == ("estimated", pytest.approx(1000 * 0.000001 + 100 * 0.000005))


def test_a_local_openai_compatible_server_is_never_looked_up(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig.model_validate(
        {
            "models": TEST_MODELS | {"critic": "lab:gpt-6.1-terra"},
            "providers": {"lab": {"kind": "openai", "base_url": "http://localhost:8000/v1"}},
        }
    )
    agent = CallbackAgent([("llm", 1000, 100, None, "gpt-6.1-terra")], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=no_fetch_price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert (row["cost_source"], row["cost_usd"]) == ("unknown", 0.0)


def test_an_estimated_direct_cost_counts_toward_the_run_budget(conn, artifacts, critic_packet, tmp_path):
    from phil.store.telemetry import run_totals

    config = PhilConfig(models=TEST_MODELS | {"critic": "openai:gpt-6.1-terra"})
    agent = CallbackAgent([("llm", 1000, 100, None, "gpt-6.1-terra")], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=direct_price_book(tmp_path), run_id="r-0001")
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    assert run_totals(conn, "r-0001").cost_usd == pytest.approx(0.0015)
```

`test_a_provider_with_one_price_is_not_estimated` configures a `lab` provider of kind `openai` with no `base_url`, which is OpenAI's own API and so is now looked up: add `prices=price_book(tmp_path)` to its `accounting_context(...)` call (the fixture has no `openai/llama`, so it stays unknown). Check `run_totals`'s real signature in `src/phil/store/telemetry.py` and `accounting_context`'s `run_id` keyword (via `context(...)` overrides) before running; adjust the call, not the assertion.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_invoke.py`
Expected: the two direct-provider tests and the run-budget test fail with `cost_source == "unknown"`; the provider-prices test fails with the book's 0.003.

- [ ] **Step 3: Implement** in `src/phil/agents/invoke.py`

Import `book_ids` alongside `PriceBook, default_price_book`. Replace `_price_book` with:

```python
def shared_price_book() -> PriceBook:
    """The process-wide price book, created (and fetched) on first use."""
    global _default_prices
    with _default_prices_lock:
        if _default_prices is None:
            _default_prices = default_price_book()
        return _default_prices


def price_book_for(ctx: AgentContext) -> PriceBook:
    """The context's price book, else the process-wide one."""
    return ctx.prices if ctx.prices is not None else shared_price_book()
```

Replace `_pricing_models` and `_estimate` with:

```python
def _book_ids(configured: str, provider: ProviderSpec | None, call_model: str | None) -> list[str]:
    """OpenRouter ids to price a call by. For an OpenRouter-kind provider (or none: the configured
    model is `openrouter:<id>`) the call's own reported model comes first — a deep agent's sub-agent,
    or a fallback route, can report a model from another vendor, and the book may know it — then the
    configured one. A direct provider is priced by its configured model (spec 2026-11-09 §4.2)."""
    name = split_model(configured)[1]
    if provider is not None and provider.kind != "openrouter":
        return book_ids(provider.kind, provider.base_url, name)
    if provider is None and not configured.startswith("openrouter:"):
        return []
    ids = [call_model] if call_model and "/" in call_model and call_model != name else []
    return [*ids, name]


def _estimate(ctx: AgentContext, configured: str, provider: ProviderSpec | None, call: ModelCall) -> float | None:
    """A call's cost with none reported (spec 2026-11-09 §4.1): the provider's own prices
    (`[providers.x]`, or a built-in's such as Ollama's 0.0), then the price book, else `None` (unknown)."""
    if provider is not None:
        configured_cost = estimate_cost(provider, call.input_tokens, call.output_tokens)
        if configured_cost is not None:
            return configured_cost
    ids = _book_ids(configured, provider, call.model)
    price = price_book_for(ctx).find(ids) if ids else None
    if price is None:
        return None
    return price.cost(call.input_tokens, call.output_tokens, call.cache_read_tokens, call.cache_write_tokens)
```

Import `ModelCall` from `phil.agents.collector`. In `_usage`, the estimate call becomes `estimate = _estimate(ctx, configured, provider, model_call)`. Remove any now-unused imports.

- [ ] **Step 4: Update the guide** (`docs/guide.md`)

Replace the paragraph at lines 185-189 ("Prices, for estimating a call's cost when it reports none: …") with:

```markdown
Prices, for estimating a call's cost when it reports none: a provider's own `input_per_mtok` /
`output_per_mtok` (USD per million tokens) under `[providers.<name>]` come first; Ollama's
built-in prices are `0.0`, so it costs $0. Otherwise the built-in `anthropic`, `openai` and
`google` providers, and any provider of kind `openrouter`, are priced from OpenRouter's public
price list, cache and long-context prices included. A provider with its own `base_url` (a proxy,
a local server) is never priced from the list. Anything else shows as unknown (see Observability).
```

In the Observability paragraph at lines 448-454, replace its text from "When a call reports no cost, Phil estimates one." to "never estimated." with:

```markdown
When a call reports no cost, Phil estimates one: from the provider's own `input_per_mtok` /
`output_per_mtok` under `[providers.<name>]` when both are set, else from OpenRouter's public
price list (`GET https://openrouter.ai/api/v1/models`, no key needed, cached under
`~/.phil/cache/` for a day) for the built-in `anthropic`, `openai` and `google` providers and any
provider of kind `openrouter`. Tokens read from or written to a prompt cache are charged at the
list's cache prices. Ollama's built-in prices are `0.0` / `0.0`, so it costs $0. A model with no
price shows as unknown, never estimated.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`
Expected: the whole suite passes. Any test that now fails with "a test attempted to fetch prices from the network" uses an `anthropic:`/`openai:`/`google:` model with a fake agent that reports no cost: give its context a fixture `PriceBook` (`prices=...`), never weaken the guard.

- [ ] **Step 6: Commit**

Message: `Costs: configured prices first, then the price list for direct providers, cache tokens at cache prices`. Stage `src/phil/agents/invoke.py tests/agents/test_invoke.py docs/guide.md` and any test file Step 5 changed.

---

### Task 4: Which models can't be priced, and the notice text

**Files:**
- Create: `src/phil/agents/unpriced.py`
- Test: `tests/agents/test_unpriced.py`

**Interfaces:**
- Consumes: `book_ids`, `PriceBook.find`, `Price` (Task 1); `ProviderSpec`, `provider_for_model`, `split_model`, `SYSTEMONE` (`phil.agents.providers`); `PhilConfig.model_for`, `model_owner` (`phil.config`).
- Ruling (plan vs spec §4.2): the spec says setup's model choice shows the matched price; setup's check step prints each model's `phil models check` line, so Task 6 shows the price there rather than in the picker — same visibility, one code path.
- Produces:
  - `PLANNING_ROLES = ("classifier", "orchestrator", "designer", "architect", "critic")`
  - `Unpriced(model: str, provider: str, roles: tuple[str, ...])` (frozen dataclass)
  - `unpriced_models(config: PhilConfig, roles: Sequence[str], book: PriceBook) -> list[Unpriced]` (never raises)
  - `run_notice(items: Sequence[Unpriced], *, max_cost_usd: float, max_tokens: int) -> str | None`
  - `planning_notice(items: Sequence[Unpriced], *, max_cost_usd: float) -> str | None`
  - `price_label(config: PhilConfig, model: str, book: PriceBook) -> str | None` (never raises)
  - `usd(value: float) -> str`

- [ ] **Step 1: Write the failing tests** (`tests/agents/test_unpriced.py`)

```python
import pytest

from phil.agents.pricing import PriceBook
from phil.agents.unpriced import (
    PLANNING_ROLES, Unpriced, planning_notice, price_label, run_notice, unpriced_models, usd,
)
from phil.config import RUN_ROLES, PhilConfig

LIST = {
    "data": [
        {"id": "anthropic/claude-sonnet-5.5", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
        {"id": "openai/gpt-6-sol", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
    ]
}


def book(tmp_path, data=LIST):
    return PriceBook(tmp_path / "prices.json", fetch=lambda: data, clock=lambda: 1_000_000.0)


def no_fetch(tmp_path):
    def boom():
        raise AssertionError("the price list should not have been consulted")

    return PriceBook(tmp_path / "unused.json", fetch=boom, clock=lambda: 1_000_000.0)


def config(models, providers=None):
    return PhilConfig.model_validate({"models": models, "providers": providers or {}})


def test_a_direct_model_the_list_has_is_priced(tmp_path):
    assert unpriced_models(config({"high": "anthropic:claude-sonnet-5-5", "low": "anthropic:claude-sonnet-5-5"}),
                           RUN_ROLES, book(tmp_path)) == []


def test_a_direct_model_the_list_lacks_is_unpriced_with_its_roles(tmp_path):
    cfg = config({"high": "anthropic:claude-x", "low": "openai:gpt-6-sol"})
    assert unpriced_models(cfg, ("architect", "implementer", "critic"), book(tmp_path)) == [
        Unpriced("anthropic:claude-x", "anthropic", ("architect", "critic")),
    ]


def test_openrouter_ollama_and_configured_prices_are_never_unpriced(tmp_path):
    cfg = config(
        {"high": "openrouter:vendor/not-listed", "low": "ollama:qwen3:8b", "tester": "lab:llama"},
        {"lab": {"kind": "openai", "base_url": "http://localhost:8000/v1", "input_per_mtok": 1.0, "output_per_mtok": 2.0}},
    )
    assert unpriced_models(cfg, ("architect", "implementer", "tester"), no_fetch(tmp_path)) == []


def test_a_local_server_without_prices_is_unpriced_without_a_lookup(tmp_path):
    cfg = config({"high": "ollama:x", "low": "lab:llama"}, {"lab": {"kind": "openai", "base_url": "http://localhost:8000/v1"}})
    assert unpriced_models(cfg, ("implementer",), no_fetch(tmp_path)) == [Unpriced("lab:llama", "lab", ("implementer",))]


def test_an_anthropic_proxy_is_unpriced_and_named(tmp_path):
    cfg = config({"high": "anthropic:claude-sonnet-5-5", "low": "anthropic:claude-sonnet-5-5"},
                 {"anthropic": {"base_url": "https://proxy.example.com"}})
    items = unpriced_models(cfg, ("implementer",), no_fetch(tmp_path))
    assert items == [Unpriced("anthropic:claude-sonnet-5-5", "anthropic", ("implementer",))]
    assert "`[providers.anthropic]`" in run_notice(items, max_cost_usd=2.0, max_tokens=1_500_000)


def test_roles_without_a_model_or_with_an_unknown_provider_are_skipped(tmp_path):
    cfg = config({"low": "nowhere:model"})
    assert unpriced_models(cfg, ("architect", "implementer"), no_fetch(tmp_path)) == []


def test_the_run_notice_for_one_model():
    items = [Unpriced("anthropic:claude-x", "anthropic", ("architect", "tester"))]
    assert run_notice(items, max_cost_usd=2.0, max_tokens=1_500_000) == (
        "No price for `anthropic:claude-x` (architect, tester), so the $2.00 run budget can't count it. "
        "The run still stops at 1,500,000 tokens. "
        "To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.anthropic]`."
    )


def test_the_run_notice_for_several_models_names_each_provider_once():
    items = [
        Unpriced("anthropic:claude-x", "anthropic", ("implementer",)),
        Unpriced("anthropic:claude-y", "anthropic", ("tester",)),
        Unpriced("lab:llama", "lab", ("reviewer",)),
    ]
    assert run_notice(items, max_cost_usd=5.0, max_tokens=100_000) == (
        "No price for `anthropic:claude-x` (implementer), `anthropic:claude-y` (tester), `lab:llama` (reviewer), "
        "so the $5.00 run budget can't count them. The run still stops at 100,000 tokens. "
        "To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.anthropic]` and `[providers.lab]`."
    )


def test_the_planning_notice_names_the_planning_budget():
    items = [Unpriced("lab:llama", "lab", ("architect",))]
    assert planning_notice(items, max_cost_usd=1.0) == (
        "No price for `lab:llama` (architect), so this goal's $1.00 planning budget (chat.max_cost_usd) "
        "can't count it. To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.lab]`."
    )


def test_no_unpriced_models_means_no_notice():
    assert run_notice([], max_cost_usd=2.0, max_tokens=1) is None
    assert planning_notice([], max_cost_usd=1.0) is None


def test_planning_roles_are_the_goal_pipelines():
    assert PLANNING_ROLES == ("classifier", "orchestrator", "designer", "architect", "critic")


@pytest.mark.parametrize(
    ("model", "providers", "expected"),
    [
        ("anthropic:claude-sonnet-5-5", None, "$2.00/$10.00 per M"),
        ("anthropic:claude-x", None, "no price"),
        ("ollama:qwen3:8b", None, "$0.00/$0.00 per M"),
        ("openrouter:openai/gpt-6-sol", None, "$2.00/$10.00 per M"),
        ("openrouter:vendor/not-listed", None, "price reported per call"),
        ("typesafe:jev", None, None),
        ("nowhere:model", None, None),
    ],
)
def test_price_label(tmp_path, model, providers, expected):
    assert price_label(config({"low": model}, providers), model, book(tmp_path)) == expected


def test_usd_shows_small_prices_to_three_places():
    assert (usd(0.0), usd(0.1), usd(2.0), usd(0.042)) == ("$0.00", "$0.10", "$2.00", "$0.042")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_unpriced.py`
Expected: `ModuleNotFoundError: No module named 'phil.agents.unpriced'`.

- [ ] **Step 3: Implement** `src/phil/agents/unpriced.py`

```python
"""The configured models Phil can't price, the line that says so before work starts, and the price
`phil models check` shows (spec 2026-11-09 §4.2, §4.6)."""

from collections.abc import Sequence
from dataclasses import dataclass

from phil.agents.pricing import Price, PriceBook, book_ids
from phil.agents.providers import SYSTEMONE, ProviderSpec, provider_for_model, split_model
from phil.config import PhilConfig

# The chat's agents for one goal: routing, intake, design, the architect and the critic.
PLANNING_ROLES = ("classifier", "orchestrator", "designer", "architect", "critic")


@dataclass(frozen=True)
class Unpriced:
    model: str
    provider: str  # the [providers.<name>] that would fix it
    roles: tuple[str, ...]


def usd(value: float) -> str:
    return f"${value:.2f}" if value == 0 or value >= 0.1 else f"${value:.3f}"


def _configured(provider: ProviderSpec) -> bool:
    return provider.input_per_mtok is not None and provider.output_per_mtok is not None


def _book_price(provider: ProviderSpec, model: str, book: PriceBook) -> Price | None:
    return book.find(book_ids(provider.kind, provider.base_url, split_model(model)[1]))


def _priced(provider: ProviderSpec, model: str, book: PriceBook) -> bool:
    # OpenRouter reports each call's cost; decision models (Jev) keep their own price.
    if provider.kind in ("openrouter", SYSTEMONE) or _configured(provider):
        return True
    return _book_price(provider, model, book) is not None


def unpriced_models(config: PhilConfig, roles: Sequence[str], book: PriceBook) -> list[Unpriced]:
    """Each model `roles` resolve to that Phil can't price, with the roles using it, in role order.
    A role with no model or an unknown provider is skipped: other checks report those."""
    found: dict[str, tuple[str, list[str]]] = {}
    priced: dict[str, bool] = {}
    for role in roles:
        try:
            model = config.model_for(role)
            provider = provider_for_model(config, model, config.model_owner(role))
            if model not in priced:
                priced[model] = _priced(provider, model, book)
        except Exception:
            continue
        if not priced[model]:
            found.setdefault(model, (provider.name, []))[1].append(role)
    return [Unpriced(model, provider, tuple(users)) for model, (provider, users) in found.items()]


def _models(items: Sequence[Unpriced]) -> str:
    return ", ".join(f"`{item.model}` ({', '.join(item.roles)})" for item in items)


def _fix(items: Sequence[Unpriced]) -> str:
    sections = " and ".join(f"`[providers.{name}]`" for name in dict.fromkeys(item.provider for item in items))
    return f"To fix it, set `input_per_mtok` and `output_per_mtok` under {sections}."


def _them(items: Sequence[Unpriced]) -> str:
    return "it" if len(items) == 1 else "them"


def run_notice(items: Sequence[Unpriced], *, max_cost_usd: float, max_tokens: int) -> str | None:
    if not items:
        return None
    return (
        f"No price for {_models(items)}, so the ${max_cost_usd:.2f} run budget can't count {_them(items)}. "
        f"The run still stops at {max_tokens:,} tokens. {_fix(items)}"
    )


def planning_notice(items: Sequence[Unpriced], *, max_cost_usd: float) -> str | None:
    if not items:
        return None
    return (
        f"No price for {_models(items)}, so this goal's ${max_cost_usd:.2f} planning budget "
        f"(chat.max_cost_usd) can't count {_them(items)}. {_fix(items)}"
    )


def price_label(config: PhilConfig, model: str, book: PriceBook) -> str | None:
    """`$in/$out per M` for `model`, "price reported per call" for an OpenRouter model the list
    lacks, "no price" otherwise; None for a decision model or a model Phil can't resolve."""
    try:
        provider = provider_for_model(config, model, model)
        if provider.kind == SYSTEMONE:
            return None
        if _configured(provider):
            return f"{usd(provider.input_per_mtok)}/{usd(provider.output_per_mtok)} per M"
        price = _book_price(provider, model, book)
    except Exception:  # an unknown provider or a bad config: no label
        return None
    if price is not None:
        return f"{usd(price.prompt * 1e6)}/{usd(price.completion * 1e6)} per M"
    return "price reported per call" if provider.kind == "openrouter" else "no price"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_unpriced.py`
Expected: all pass. If `typesafe:jev` doesn't resolve with no TypeSafe key in `config`, it still yields `None` (decision model) — the assertion stands.

- [ ] **Step 5: Commit**

Message: `Unpriced models: found per role, with the notice that names the fix`. Stage `src/phil/agents/unpriced.py tests/agents/test_unpriced.py`.

---

### Task 5: Show the notice at run start and goal start

**Files:**
- Modify: `src/phil/run/worker.py:96-110` (new `_note_unpriced` beside `_note_policy`) and `:236` (call it)
- Modify: `src/phil/chat/controller.py:1037-1041` (`_routed`), plus a new job and handler
- Modify: `docs/guide.md` (after the `[chat] max_cost_usd` paragraph, ~line 462)
- Test: `tests/run/test_worker.py`, `tests/chat/test_planning_budget.py`

**Interfaces:**
- Consumes: `unpriced_models`, `run_notice`, `planning_notice`, `PLANNING_ROLES` (Task 4); `shared_price_book`, `price_book_for` (Task 3); `RUN_ROLES` (`phil.config`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/run/test_worker.py`:

```python
class RecordingEvents:
    def __init__(self):
        self.appended = []

    def append(self, kind, **data):
        self.appended.append((kind, data))


def _lab_config(**prices):
    return PhilConfig.model_validate({
        "models": TEST_MODELS | {"implementer": "lab:llama"},
        "providers": {"lab": {"kind": "openai", "base_url": "http://localhost:8000/v1", **prices}},
    })


def test_an_unpriced_run_model_is_noted_once_at_start(tmp_path):
    from phil.run.worker import _note_unpriced

    events = RecordingEvents()
    _note_unpriced(events, _lab_config())
    [(kind, data)] = events.appended
    assert kind == "notice"
    assert data["message"].startswith("No price for `lab:llama` (implementer), so the $2.00 run budget")
    assert "1,500,000 tokens" in data["message"] and "`[providers.lab]`" in data["message"]


def test_priced_run_models_are_not_noted():
    from phil.run.worker import _note_unpriced

    events = RecordingEvents()
    _note_unpriced(events, _lab_config(input_per_mtok=1.0, output_per_mtok=2.0))
    assert events.appended == []


def test_the_unpriced_note_never_fails_the_run(monkeypatch):
    import phil.run.worker as worker

    def boom(*args, **kwargs):
        raise RuntimeError("price list exploded")

    monkeypatch.setattr(worker, "unpriced_models", boom)
    worker._note_unpriced(RecordingEvents(), _lab_config())  # no exception


def test_a_new_run_checks_its_prices(calc_repo, monkeypatch):
    import phil.run.worker as worker

    seen = []
    monkeypatch.setattr(worker, "_note_unpriced", lambda events, config: seen.append(config))
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    run_worker(calc_repo, record.run_id, "start", factory=happy())
    assert len(seen) == 1
```

(Use the module's existing imports for `PhilConfig`, `TEST_MODELS`, `resolve_repo`, `prepare_run`, `calc_plan`, `happy`, `run_worker`; add any that are missing.)

Append to `tests/chat/test_planning_budget.py`:

```python
LAB = {"lab": {"kind": "openai", "base_url": "http://localhost:8000/v1"}}


def test_a_goal_with_an_unpriced_planning_model_says_so_once(calc_repo):
    config = PhilConfig.model_validate({"models": TEST_MODELS | {"architect": "lab:llama"}, "providers": LAB})
    text, *_ = run_chat(
        calc_repo, ["add subtract", "n"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}, config=config
    )
    flat = " ".join(text.split())
    assert flat.count("No price for `lab:llama` (architect)") == 1
    assert "planning budget (chat.max_cost_usd)" in flat and "`[providers.lab]`" in flat


def test_a_priced_goal_says_nothing_about_prices(calc_repo):
    text, *_ = run_chat(
        calc_repo, ["add subtract", "n"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]},
        config=PhilConfig(models=TEST_MODELS),
    )
    assert "No price for" not in text


def test_a_failing_price_check_doesnt_disturb_the_goal(calc_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("price list exploded")

    monkeypatch.setattr(controller_mod, "unpriced_models", boom)
    config = PhilConfig.model_validate({"models": TEST_MODELS | {"architect": "lab:llama"}, "providers": LAB})
    text, *_ = run_chat(
        calc_repo, ["add subtract", "n"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}, config=config
    )
    assert "price list exploded" not in text and "No price for" not in text
    assert "Plan CALC v1" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/run/test_worker.py tests/chat/test_planning_budget.py`
Expected: `ImportError: cannot import name '_note_unpriced'`, `AttributeError: … has no attribute 'unpriced_models'`, and the first chat test failing on the missing line.

- [ ] **Step 3: Implement the run-start note** in `src/phil/run/worker.py`

Imports: `from phil.agents.invoke import shared_price_book`, `from phil.agents.unpriced import run_notice, unpriced_models`, `RUN_ROLES` from `phil.config` (with the module's existing config import).

```python
def _note_unpriced(events: EventLog, config: PhilConfig) -> None:
    """At run start, once, in the run log: the run's models Phil can't price, so its cost budget
    can't count them (spec 2026-11-09 §4.6). Never fails the run."""
    try:
        items = unpriced_models(config, RUN_ROLES, shared_price_book())
        notice = run_notice(items, max_cost_usd=config.run.max_cost_usd, max_tokens=config.run.max_tokens)
        if notice is not None:
            events.append("notice", message=notice)
    except Exception:
        _logger.warning("couldn't check the run's model prices", exc_info=True)
```

Call it right after `_note_policy(events, info.root, paths, config)` in the `elif not snapshot.values:` branch: `_note_unpriced(events, config)`.

- [ ] **Step 4: Implement the goal-start note** in `src/phil/chat/controller.py`

Imports: `from phil.agents.invoke import price_book_for` (with the existing `phil.agents.invoke` import) and `from phil.agents.unpriced import PLANNING_ROLES, planning_notice, unpriced_models`.

In `_routed`, in the `else:` branch before `self._set_stage("intake")`: `self._unpriced_job()`. Add beside `_budget_check`:

```python
    def _unpriced_job(self) -> None:
        """Once per goal: the goal's planning models Phil can't price (spec 2026-11-09 §4.6). A side
        job (generation -1): the price list may be fetched first, and it must never touch the goal's
        step or fail it."""

        def fn(ctx: AgentContext) -> dict:
            try:
                items = unpriced_models(ctx.config, PLANNING_ROLES, price_book_for(ctx))
                return {"line": planning_notice(items, max_cost_usd=ctx.config.chat.max_cost_usd)}
            except Exception:
                _logger.warning("couldn't check the goal's model prices", exc_info=True)
                return {"line": None}

        self._job("unpriced_ready", fn, generation=-1)

    def _on_unpriced_ready(self, data: dict) -> None:
        if data.get("line"):
            self.console.print(f"[phil.warn]{escape(data['line'])}[/]")
```

If the module's logger isn't named `_logger`, use its name.

- [ ] **Step 5: Update the guide** (`docs/guide.md`)

After the `[chat] max_cost_usd` bullet list (the "**At the limit:**" bullet), add:

```markdown
When Phil can't price a model a run or a goal will use — a custom provider with no
`input_per_mtok` / `output_per_mtok`, a provider with its own `base_url`, or a model OpenRouter's
list doesn't have — it says so once, before the work starts, and goes on: for example
``No price for `lab:llama` (implementer), so the $2.00 run budget can't count it. The run still
stops at 1,500,000 tokens. To fix it, set `input_per_mtok` and `output_per_mtok` under
`[providers.lab]`.`` Nothing is blocked; the token limit still applies.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`
Expected: the whole suite passes. A chat or run test that now fails on the network guard uses a direct model: give it a fixture book (monkeypatch `phil.agents.invoke._default_prices` to a `PriceBook` with a fixture `fetch`), never weaken the guard.

- [ ] **Step 7: Commit**

Message: `Notices: a run or goal whose models have no price says so before it starts`. Stage `src/phil/run/worker.py src/phil/chat/controller.py docs/guide.md tests/run/test_worker.py tests/chat/test_planning_budget.py` and any test file Step 6 changed.

---

### Task 6: The price on `phil models check` and setup's check lines

**Files:**
- Modify: `src/phil/agents/check.py:46-53,168-206`
- Modify: `src/phil/cli/main.py:650-654`
- Modify: `src/phil/setup/flow.py:357-364,416-419`
- Modify: `docs/guide.md` (the `phil models check` paragraph, ~line 230-238)
- Test: `tests/agents/test_check.py`, `tests/setup/test_flow.py`

**Interfaces:**
- Consumes: `price_label`, `usd` (Task 4); `shared_price_book` (Task 3).
- Produces: `CheckResult.price: str | None = None`; `check_models(..., prices: PriceBook | None = None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_check.py`:

```python
from phil.agents.pricing import PriceBook

PRICE_LIST = {"data": [{"id": "openai/gpt-6-sol", "pricing": {"prompt": "0.000002", "completion": "0.00001"}}]}


def price_list(tmp_path):
    return PriceBook(tmp_path / "prices.json", fetch=lambda: PRICE_LIST, clock=lambda: 1_000_000.0)


def test_a_passing_model_shows_its_price(tmp_path):
    config = PhilConfig(models={"high": "openai:gpt-6-sol", "low": "ollama:small"})
    factory = ScriptedAgentFactory({"model_check": [OK, OK]})
    results = check_models(config, factory=factory, repo_root=tmp_path, prices=price_list(tmp_path))
    assert [(r.model, r.price) for r in results] == [
        ("openai:gpt-6-sol", "$2.00/$10.00 per M"), ("ollama:small", "$0.00/$0.00 per M"),
    ]


def test_a_failing_model_shows_no_price(tmp_path):
    config = PhilConfig(models={"high": "ollama:big", "low": "ollama:big"})
    factory = ScriptedAgentFactory({"model_check": [RuntimeError("down")]})
    [result] = check_models(config, factory=factory, repo_root=tmp_path, prices=price_list(tmp_path))
    assert not result.ok and result.price is None
```

In `tests/setup/test_flow.py`, next to the test asserting `line.startswith("✓ high")` (line ~398), add a test that a `CheckResult(..., price="$2.00/$10.00 per M")` returned by the `check` stub is printed as `✓ high  <model>  <secs>s  $2.00/$10.00 per M` (build it the way that file's existing check stubs build results), and in `tests/cli` (the `models check` command's tests; find them with `grep -rln "models\", \"check\"\|needed a reminder" tests/cli`) add the same assertion for the CLI line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_check.py tests/setup/test_flow.py tests/cli`
Expected: `TypeError: check_models() got an unexpected keyword argument 'prices'` and the line assertions failing.

- [ ] **Step 3: Implement**

`src/phil/agents/check.py`: add `price: str | None = None  # "$in/$out per M", "no price"…; None when it failed` to `CheckResult`; add `prices: PriceBook | None = None` to `check_models` (docstring: "`prices` is the price book for the result's price; the process-wide one when not given."); imports `from phil.agents.pricing import PriceBook`, `from phil.agents.unpriced import price_label`, and `shared_price_book` from `phil.agents.invoke`. In the loop:

```python
            book = prices if prices is not None else shared_price_book()
            for call, (model, labels) in enumerate(targets, 1):
                started = clock()
                ok, detail, reminded = _check_one(ctx, model, labels, call, jev_transport)
                seconds = clock() - started
                price = price_label(config, model, book) if ok else None
                results.append(CheckResult(", ".join(labels), model, ok, seconds, detail, reminded, price))
```

(The price is looked up after the clock stops, so a first fetch of the list doesn't count against the model's time.)

`src/phil/cli/main.py`, the pass line:

```python
            note = " (needed a reminder)" if result.reminded else ""
            price = f"  {escape(result.price)}" if result.price else ""
            line = f"[phil.gate.pass]✓[/] {escape(result.label)}  {escape(result.model)}  {result.seconds:.1f}s{price}{note}"
```

`src/phil/setup/flow.py`: delete `_usd` and import `usd` from `phil.agents.unpriced` (update `_describe` to call `usd`); `_result_line`'s pass branch becomes:

```python
        price = f"  {result.price}" if getattr(result, "price", None) else ""
        return f"✓ {result.label}  {result.model}  {result.seconds:.1f}s{price}"
```

- [ ] **Step 4: Update the guide** (`docs/guide.md`, the `phil models check` paragraph): add the sentence "A passing model's line ends with its price, e.g. `$2.00/$10.00 per M` (input/output per million tokens), `price reported per call` for an OpenRouter model, or `no price` when Phil can't price it (see Observability)."

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`
Expected: the whole suite passes. Check tests that pass a direct (`openai:`/`anthropic:`/`google:`) model now look up a price: pass `prices=price_list(tmp_path)` there; CLI and setup tests that run the real `check_models` with a direct model need the default book monkeypatched (`phil.agents.invoke._default_prices`) to a fixture book.

- [ ] **Step 6: Commit**

Message: `Models check: each passing model shows its price`. Stage the four source files, `docs/guide.md`, and the changed test files.

---

### Task 7: Prompt caching for direct Anthropic in the light harness

**Files:**
- Modify: `src/phil/agents/factory.py:206-243` (`_build_light_agent`), plus a `_provider_kind` helper
- Modify: `docs/guide.md:469` (the prompt-caching paragraph)
- Test: `tests/agents/test_factory.py`

**Interfaces:**
- Consumes: `resolve_provider`, `split_model`, `ProviderSpec` (already imported in factory.py).

- [ ] **Step 1: Write the failing tests** (append to `tests/agents/test_factory.py`)

```python
from phil.agents.providers import BUILTIN_PROVIDERS, ProviderSpec


def _light_middleware(tmp_path, monkeypatch, model, provider=None):
    import langchain.agents

    captured = {}
    monkeypatch.setattr(langchain.agents, "create_agent", lambda model, tools=None, **kw: captured.update(kw) or "agent")
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: object())
    build_agent(get_spec("architect"), model, tmp_path, [], provider=provider)
    return [type(m).__name__ for m in captured["middleware"]]


def test_a_light_agent_on_anthropic_caches_its_prompt(tmp_path, monkeypatch):
    names = _light_middleware(tmp_path, monkeypatch, "anthropic:claude-sonnet-5-5", BUILTIN_PROVIDERS["anthropic"])
    assert names[-1] == "AnthropicPromptCachingMiddleware"


def test_a_custom_anthropic_kind_provider_caches_too(tmp_path, monkeypatch):
    proxy = ProviderSpec("proxy", "anthropic", "https://proxy.example.com", "PROXY_KEY", None, None)
    assert "AnthropicPromptCachingMiddleware" in _light_middleware(tmp_path, monkeypatch, "proxy:claude-x", proxy)


def test_a_built_in_anthropic_model_without_a_resolved_provider_caches(tmp_path, monkeypatch):
    assert "AnthropicPromptCachingMiddleware" in _light_middleware(tmp_path, monkeypatch, "anthropic:claude-sonnet-5-5")


@pytest.mark.parametrize("model", ["openai:gpt-6-sol", "openrouter:anthropic/claude-sonnet-5.5", "ollama:qwen3:8b"])
def test_other_light_agents_dont_get_anthropics_cache_middleware(tmp_path, monkeypatch, model):
    assert "AnthropicPromptCachingMiddleware" not in _light_middleware(tmp_path, monkeypatch, model)


def test_a_lean_agent_on_anthropic_doesnt_cache(tmp_path, monkeypatch):
    import langchain.agents

    captured = {}
    monkeypatch.setattr(langchain.agents, "create_agent", lambda model, tools=None, **kw: captured.update(kw) or "agent")
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: object())
    build_agent(get_spec("critic"), "anthropic:claude-sonnet-5-5", tmp_path, [], provider=BUILTIN_PROVIDERS["anthropic"])
    assert "AnthropicPromptCachingMiddleware" not in [type(m).__name__ for m in captured["middleware"]]


def test_deepagents_still_caches_a_deep_agent_and_its_sub_agent(tmp_path, monkeypatch):
    # The deep harness gets Anthropic caching from deepagents itself, for the main agent and the
    # general-purpose sub-agent; this pins that it still does.
    from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

    from phil.agents.providers import build_chat_model

    made = []
    original = AnthropicPromptCachingMiddleware.__init__

    def spy(self, *args, **kwargs):
        made.append(kwargs.get("unsupported_model_behavior"))
        original(self, *args, **kwargs)

    monkeypatch.setattr(AnthropicPromptCachingMiddleware, "__init__", spy)
    model = build_chat_model(BUILTIN_PROVIDERS["anthropic"], "claude-sonnet-5-5", 30, environ={"ANTHROPIC_API_KEY": "dummy"})
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    build_agent(get_spec("implementer"), "anthropic:claude-sonnet-5-5", tmp_path, [], provider=BUILTIN_PROVIDERS["anthropic"])
    assert len(made) >= 2 and set(made) == {"ignore"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/agents/test_factory.py`
Expected: the three caching tests fail (`AnthropicPromptCachingMiddleware` not in the list); the "other", lean and deep tests pass already (they pin behaviour that must not change).

- [ ] **Step 3: Implement** in `src/phil/agents/factory.py`

```python
def _provider_kind(model: str, provider: ProviderSpec | None) -> str | None:
    """The kind of `model`'s provider: `provider`'s, else the built-in its prefix names; None if unknown."""
    from phil.config import ConfigError, PhilConfig

    if provider is not None:
        return provider.kind
    try:
        return resolve_provider(PhilConfig(), split_model(model)[0]).kind
    except ConfigError:
        return None
```

In `_build_light_agent`, after `middleware.append(answer_reminder_middleware(spec.out_contract, tools=True))`:

```python
    if _provider_kind(model, provider) == "anthropic":
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

        # Direct Anthropic caches only what's marked: this marks the growing history, so the agent's
        # tool loop rereads it at the cache price (spec 2026-11-09 §4.7). Last, so it marks the
        # request the model actually gets.
        middleware.append(AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"))
```

- [ ] **Step 4: Update the guide** (`docs/guide.md:469`): replace the paragraph with:

```markdown
Anthropic models cache their prompts, so an agent's repeated history is billed at the cache price: through OpenRouter, and directly for agents that use tools (the architect, the quick implementer, the designer and answers, and every deep agent). Other vendors cache on their own. `phil show` and `/show` list cache reads next to input tokens, for example `172,000 (128,000 cached)`.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`
Expected: the whole suite passes.

- [ ] **Step 6: Commit**

Message: `Light agents on direct Anthropic cache their prompts`. Stage `src/phil/agents/factory.py tests/agents/test_factory.py docs/guide.md`.

---

## After the tasks

Live acceptance (spec §5), by Claude with the stored keys and a throwaway `PHIL_HOME`, never `pytest -m live` or `-m bench`:

1. One small quick-path run each on Anthropic, OpenAI and Google with setup's suggested models: `phil runs` / `phil show` show a dollar cost with the `~` estimate mark.
2. On Anthropic, `phil show` reports cached input tokens for the light-harness agents.
3. One run on a direct provider with `--set run.max_cost_usd=0.02` stops at the budget.
4. A custom provider with no prices shows the notice at goal start and run start.
5. `phil models check` lists a price on each passing line.

The user runs the OpenRouter benchmark.
