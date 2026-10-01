# Plan M3a follow-ups

Deferred items from implementing plan M3a ("routing"). Plan:
`2026-09-30-phil-m3a-routing.md`. Spec:
`../specs/2026-09-30-phil-m3-proportional-orchestration-design.md`. Ledger:
`.superpowers/sdd/2026-09-30-phil-m3a-routing/progress.md`.

## From the task brief

- **Jev calls aren't in run or chat telemetry**, because there are no prices yet for a
  typesafe model. `judge_jev` doesn't go through `invoke_agent`, so its tokens and latency
  never reach the `telemetry` table; `phil show`/`phil runs` cost totals don't see routing
  calls made through Jev.
- **The classify primitive could be reused** for other decisions: TDD versus check mode,
  the chat's reply intent (answer vs. a new goal while a run is in progress), and whether a
  review finding blocks. `phil.routing.classify` is built generically enough for this (spec
  §8), but nothing calls it from those sites yet.
- **A Jev-specific cost estimate**, once TypeSafe publishes prices for `jev-latest`. Until
  then `judge_jev`'s `Usage` always has `cost_usd=None`, and the classifier benchmark's
  `cost_per_100` for the `jev` backend is always `None` (the decision rule reports
  "undecided (cost unknown)" rather than yes/no).
- **The thresholds are to be set from the first benchmark run.** `routing.confidence_threshold`
  (0.5) and `routing.detail_threshold` (0.6) are spec defaults, not benchmark-tuned; rerun
  `python -m tests.live.bench.classify.run --report` once real models are configured and
  adjust the defaults (and `[routing]` in `README.md`) from the sweep table.

## From the task reviews

- **deepagents' eviction of large tool results** writes `/large_tool_results/...` into the
  backend root and skips write permissions. The light harness (`phil.agents.factory`) turns
  this off; the read-only deep agents -- the answerer (spec §3.6), and the architect reading
  its snapshot -- may still have it enabled. Check each read-only deep agent's middleware
  stack and disable eviction there too, since a write into the backend root defeats
  "read-only."
- **The run's depth is recorded only in the chat transcript's `route` event note.** Spec
  §3.5 wants it on the run itself. This needs a `runs` table migration (a `depth` column)
  and a `phil show` line printing it; natural to pick up in M3b alongside the quick engine.
- **`classify()` returns `None` when both Jev and the LLM backend fail**, and the controller
  only prints "Router unavailable" when Jev fails and falls back to an LLM that then
  succeeds (`fallback_reason` is set on the judgement). When the LLM backend also fails,
  `classify()` returns `None` silently and intake decides with no visible explanation --
  the "Router unavailable" line is lost for that case. Worth a status line for the
  double-failure path too.
- **A forced `/ask` diagnosis never offers a fix**, and a bare `/full` at the fix prompt
  (spec §3.6: "Fix it? (Enter = quick fix, /full = plan it)") prints the command's usage
  instead of accepting it, because `/full` is currently only recognised as a routing
  override on a new goal, not as an answer to the fix prompt. Spec §3.6's "/full = plan it"
  needs its own handling at that prompt; natural for M3b, alongside the quick path it would
  start.
- **The answerer's capped call turns a string system prompt into list content blocks** on
  its last (12th) call, to attach the cap notice. Some OpenAI-compatible servers (older
  Ollama or vLLM builds) reject a list-content system message. Worth a compatibility check
  or a plain-string fallback before this reaches users on those backends.
- **`controller.py` is about 1530 lines.** The routing (`_route_job`/`_on_route_ready`/
  `_routed`/`_status_line`) and answer (`_answer_job`/...) methods could move into a helper
  module (e.g. `phil/chat/routing.py`) to keep the controller focused on orchestration.
- **`test_route_state`'s file count depends on the autouse `gitconfig` fixture** writing
  into `tmp_path` (it adds a tracked file that `route_state`'s `git ls-files` then counts).
  The test passes today, but it's coupled to a fixture it doesn't name; worth asserting
  against the fixture's own file list instead of a hardcoded count, so a change to the
  fixture doesn't silently break an unrelated test.
