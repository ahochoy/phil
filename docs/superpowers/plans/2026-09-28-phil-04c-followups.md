# Plan 4c (Observability and reliability): Follow-ups for Later Plans

Deferred findings from plan 4c's task reviews. Earlier follow-ups: `2026-09-24-phil-04a-followups.md`
(its "Plan 4c — carried items", "Before a public release" and "Dependency policy" sections still
apply — the lean read-only architect stays deferred there), `2026-09-28-phil-04b-followups.md`.

## PriceBook (`phil.agents.pricing`)

- **A malformed cache is retried on every call, not just once.** `PriceBook._load` (pricing.py:95)
  only sets `self._loaded = True` when it ends up with parsed data. If the cache file exists but
  is malformed (or a fresh fetch also fails), `data` stays `None`, `_loaded` is never set, and the
  book falls back to the `_retry_after_failure_s` cool-down — which retries the same failing fetch
  + cache read forever (rate-limited, but never resolved) instead of settling on "no prices for
  this process" once a malformed cache has been seen and a fetch has failed. Rewriting a good
  cache later (e.g. the next successful fetch) still self-heals, so this only matters while both
  the cache and the network are unavailable.
- **Two cache reads on a fetch failure.** `_load` calls `_read_fresh_cache()` (pricing.py:69,
  which itself calls `_read_cache_envelope()`) to decide whether to fetch, then — if the fetch
  fails — calls `_read_stale_cache()` (pricing.py:78), which reads the same file from disk again.
  Read the envelope once and reuse it for both the freshness check and the stale fallback.

## Cost and retry accounting (`phil.agents.invoke`)

- **`calls.model` mixes reported model ids and configured provider strings.** `_usage` sets
  `model=model_call.model or configured` (invoke.py:126): when the collector's callback reports a
  model name (the common case), the column holds a bare id like `openai/gpt-6-luna`; when it
  doesn't (a scripted fake, or a provider that omits it), the column holds the full configured
  string like `openrouter:openai/gpt-6-luna`. `phil show`'s usage table doesn't render this column
  today, but anything that groups or displays `calls.model` directly will see two formats for the
  same model.
- **Retry count is tracked two different ways.** The success path takes `retries` straight from
  `call_with_retry`'s return value (invoke.py:269); the exception path instead recomputes it as
  `len(sleeps)` from the `counting_sleep` wrapper (invoke.py:273) because `call_with_retry` raises
  before returning its own count. Both should agree by construction (each transient retry sleeps
  exactly once), but the duplication is fragile — a change to one path's counting logic can drift
  from the other silently. Consider having `call_with_retry` attach the count to the raised
  exception instead.

## Test infrastructure (`tests/conftest.py`)

- **A tripped network guard reports as a fixture teardown error, not a test failure.**
  `no_price_fetch` (conftest.py:41) is an autouse generator fixture; when a test does trigger the
  guarded fetch, `guard_against_price_fetches` calls `pytest.fail(...)` after the `yield`
  (conftest.py:37) — during fixture teardown. Pytest reports this as an *error* on the test
  (teardown phase) rather than a *failure*, which is easy to misread in a full-suite summary as
  an unrelated fixture problem instead of "this test touched the network."

## Run engine (`phil.run.engine`)

- **A crash between the budget-warning append and its checkpoint can print the warning twice.**
  `_budget_check` appends the `budget_warning` event to the run's event log immediately
  (`self.deps.events.append(...)`, engine.py) but only *returns* `{"budget_warned": True}` for
  LangGraph to persist in the node's checkpoint afterwards. A crash or kill between the append and
  the next checkpoint write means `budget_warned` is still unset on resume, so the same threshold
  crossing is detected again and the event is appended (and shown) a second time. Low-impact (an
  extra warning line, not a correctness issue) but worth a note if resume behavior is revisited.

## `phil show` (`phil.ui.show_view`)

- **Tasks are read by parsing `summary.md`'s Markdown.** `_tasks_section` (show_view.py:75) looks
  for a literal `"## Tasks"` line and grabs everything until the next `"## "` heading. It falls
  back to `plan.json` when there's no summary yet, but once a summary exists, any change to its
  heading text or structure silently breaks task rendering in `phil show` (no test would catch a
  copy edit to the heading). Consider a small structured sidecar (or re-deriving from `plan.json`
  plus task state) instead of scraping the human-readable summary.
- **Detail refs are ordered by file mtime, not a stable sequence.** `_newest_first` (show_view.py:35)
  sorts by `(p.stat().st_mtime, p.name)`. Filesystem mtime resolution (1s on some setups) and
  clock skew across processes/threads writing artifacts concurrently can put two refs in the wrong
  relative order, or make ordering non-deterministic when two files land in the same tick. A
  monotonic write-order counter (or numbering artifacts on write) would be more reliable than
  relying on mtime.
