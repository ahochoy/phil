# Prices and budgets on every provider — design

Status: approved in conversation, 2026-11-09. Release work item "confirm the bring-your-own-provider
path (M2a)" in `docs/superpowers/roadmap.md`, second of the two specs from the provider spike (the
first: `2026-10-22-phil-provider-answers-design.md`).

## 1. Goal

A user on Anthropic, OpenAI or Google directly sees what each call cost, and their cost budgets
(`[run] max_cost_usd`, `[chat] max_cost_usd`) stop work the way they do on OpenRouter. Direct
Anthropic caches prompts in every agent loop, and cached tokens are charged at their cached price.
When Phil can't price a model, the user is told, once, before the work starts. Nothing that works
on OpenRouter today gets worse.

## 2. Where things stand

- **Budgets exist.** `[run] max_cost_usd` (default $2.00) and `[chat] max_cost_usd` ($1.00) warn
  at `[run] warn_at` (80%) and stop; `[run] max_tokens` (1,500,000) caps tokens.
- **Only some calls have a cost.** OpenRouter reports a cost on each response. The price book
  (`src/phil/agents/pricing.py`) holds OpenRouter's public model list and answers only for
  `openrouter:` models. Ollama is priced at $0, and `[providers.x]` can set `input_per_mtok` /
  `output_per_mtok`. Built-in Anthropic, OpenAI and Google have no price: each call is recorded
  as cost source `unknown` and counts as $0, so neither cost budget ever fires on them; only the
  token cap stops a run.
- **Caching.** OpenRouter's `anthropic/` models get one `cache_control` breakpoint
  (`_build_openrouter` in `src/phil/agents/providers.py`). Direct Anthropic is cached only in the
  deep harness, by the `AnthropicPromptCachingMiddleware` deepagents adds; the light and lean
  harnesses send no cache markers.
- **Cached tokens are charged in full.** The collector records `cache_read_tokens`, but an
  estimate prices every input token at the prompt price. Cache writes aren't recorded at all.

The public list (fetched 2026-11-09, 463 models) carries the direct models under OpenRouter ids,
e.g. `anthropic/claude-sonnet-5.5`, `openai/gpt-6.1-terra`, `google/gemini-3.8-flash`, with
`input_cache_read`, `input_cache_write` and, for some models, long-context `overrides`
(`min_prompt_tokens` 272,000 on OpenAI's, 100,000 on `claude-haiku-5.5`). It also lists
discounted `:batch` variants.

## 3. Decisions (user, 2026-11-09)

- Prices for direct providers come from OpenRouter's public model list (approach A: extend the
  existing price book), with `[providers.x]` prices overriding it.
- A model with no price: say so once and carry on; nothing is blocked.
- Prompt caching for direct Anthropic in the light harness; not in the lean harness.

## 4. Design

### 4.1 Where a call's price comes from

`invoke._estimate` prices each model call in this order:

1. **Reported**: a cost on the response (today only OpenRouter's). Source `reported`.
2. **Configured**: both `input_per_mtok` and `output_per_mtok` set on the provider, from
   `[providers.x]` or a built-in (Ollama's 0.0, TypeSafe's). Source `estimated`.
3. **Price book**: for providers of kind `openrouter`, `anthropic`, `openai` and `google`.
   Source `estimated`.
4. **Unknown**: cost 0.0, source `unknown`, as today.

Step 2 now comes before step 3. Today the book wins for OpenRouter; afterwards a price the user
set always wins.

### 4.2 Matching a direct model to the list

`PriceBook.price(model)` accepts any `provider:name` model with a provider spec. For kind
`openrouter` the id is the name, as today. For the built-in kinds `anthropic`, `openai` and
`google`, the vendor prefix is the kind (`anthropic/`, `openai/`, `google/`), and the name is
tried, first match wins:

1. as is: `openai:gpt-6.1-terra` → `openai/gpt-6.1-terra`;
2. a `-` between two digits becomes `.`: `anthropic:claude-sonnet-5-5` → `anthropic/claude-sonnet-5.5`;
3. a trailing `-YYYYMMDD` dropped, then rule 2: `claude-haiku-4-5-20251001` → `anthropic/claude-haiku-4.5`.

Never matched: ids ending in `:batch` (a separate, discounted price), and router entries with
OpenRouter's `-1` sentinel (already skipped). A provider of kind `openai` with a custom
`base_url` (Ollama, vLLM, llama.cpp, any self-hosted endpoint) is never looked up: its model
names would match another vendor's price. The same goes for kind `anthropic` or `google` with a
custom `base_url` (a proxy may charge anything).

`phil models check` and setup's model choice show the matched price next to a direct model, as
they do for OpenRouter models today, so a wrong match is visible.

### 4.3 Fetching

Unchanged mechanics: fetched on the first call that needs a price, cached in
`~/.phil/cache/openrouter-models.json` for 24 hours, a stale cache used when a fetch fails, and a
10-minute cool-down before retrying with no cache at all. New: a user of only direct providers
now makes that one daily, keyless request to openrouter.ai. Setting both prices in
`[providers.x]` means that provider's calls never consult the book. No setting turns the lookup
off.

### 4.4 Token counts

The collector (`src/phil/agents/collector.py`) reads `usage_metadata.input_token_details`:
`cache_read` as today, plus `cache_creation` as `cache_write_tokens`. Both are part of
`input_tokens` on all three vendors. A telemetry migration adds `cache_write_tokens` (INTEGER,
default 0) next to `cache_read_tokens`; existing databases keep working.

### 4.5 The estimate

`Price` gains `cache_read` and `cache_write` (USD per token, `None` when the list has none) and the
entry's `overrides` as tiers. For one call:

```
uncached = input_tokens − cache_read_tokens − cache_write_tokens   (never below 0)
cost     = uncached           × prompt
         + cache_read_tokens  × (cache_read  or prompt)
         + cache_write_tokens × (cache_write or prompt)
         + output_tokens      × completion
```

- **Tiers.** If `input_tokens` is at least an override's `min_prompt_tokens`, that override's
  prices (falling back to the base entry's for any it omits) apply to the whole call; of several,
  the highest threshold wins.
- **Configured prices** (step 2) have no cache or tier fields: all input at `input_per_mtok`,
  output at `output_per_mtok`, as today.
- **Not priced:** Anthropic's 1-hour cache write (Phil only writes the 5-minute cache), and
  reasoning tokens separately (they are inside `output_tokens` on OpenAI and Google).
- No rounding until display (2 decimals, as today).

An estimated cost counts toward `max_cost_usd` exactly like a reported one, and is shown with the
existing estimate mark in the status bar, `phil show` and the budget warnings.

### 4.6 Telling the user about a model with no price

When a run starts (from the chat or `phil run`) and when a goal starts planning in the chat, Phil
checks every role's configured model. A model is unpriced when its provider doesn't report costs
(kind `openrouter` does), it has no configured prices (4.1 step 2), and the price book has no
match. This is also where the list is first fetched, so starting Phil never waits on it.

If any are unpriced, Phil shows one warning line for the run or goal, listing them all:

> No price for `anthropic:claude-x` (architect, tester), so the $2.00 run budget can't count it.
> The run still stops at 1,500,000 tokens. To fix it, set `input_per_mtok` and `output_per_mtok`
> under `[providers.anthropic]`.

For planning, the line names the planning budget (`chat.max_cost_usd`) and leaves out the token
sentence. Several providers: one `[providers.x]` per provider in the fix. Nothing is blocked. A
model seen only at run time (a sub-agent reporting a model nobody configured) gets no line; its
calls show as unknown.

### 4.7 Prompt caching on direct Anthropic

- **Light harness:** `AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")` from
  `langchain_anthropic` is added to the agent's middleware when the provider's kind is
  `anthropic` (a custom `[providers.x]` with `kind = "anthropic"` included).
- **Lean harness:** unchanged. A lean agent makes one call, which would pay the 1.25× cache-write
  price with nothing to read it back.
- **Deep harness and its general-purpose sub-agent:** unchanged; deepagents already adds the
  middleware. A test pins it.
- **OpenRouter:** the `cache_control` breakpoint stays as is.

## 5. Tests

Offline, each written to fail first:

- Matching: each rule in 4.2; `:batch` and sentinel entries never matched; kind `openai` with a
  custom `base_url` never looked up; kind `anthropic` with a custom `base_url` never looked up.
- Price order: reported, then configured, then the book, then unknown; a configured price beats
  the book for an `openrouter:` model.
- The estimate: cache reads and writes at their prices; a missing cache price falls back to the
  prompt price; tier selection, including several qualifying tiers and an override missing a
  field; `uncached` never below 0.
- Collector reads `cache_creation`; the migration adds `cache_write_tokens` and an old database
  still reads.
- The notice: shown for an unpriced direct model and a priceless custom provider; not shown for
  OpenRouter, Ollama, configured prices or a book match; names `[providers.<name>]` per provider;
  the planning variant names `chat.max_cost_usd`; shown once per run or goal.
- Middleware by harness and provider kind: light + `anthropic` gets the caching middleware; light
  + other kinds and lean + any kind don't; deep still does.
- A run on a direct provider with estimated costs stops at `max_cost_usd`.
- `phil models check` and setup show a direct model's matched price.

Live acceptance, run by Claude with the user's stored keys and a throwaway `PHIL_HOME` (never
`pytest -m live` or `-m bench`):

- One small quick-path run each on Anthropic, OpenAI and Google, with setup's suggested models:
  the cost shows in dollars with the estimate mark, not as unknown.
- On Anthropic, `phil show` reports cached input tokens for the light-harness agents.
- A run on a direct provider with `--set run.max_cost_usd=0.02` stops at the budget.
- A custom provider with no prices shows the notice.
- The user runs the OpenRouter benchmark; pass rates and cost per task stay within the last
  baseline's noise.

## 6. Out of scope

- Per-role, per-day or per-month spend caps.
- A setting to turn off the price lookup.
- Prices for `typesafe` / Jev (it keeps its built-in price).
- Anthropic's 1-hour cache.
- Caching for lean agents.
