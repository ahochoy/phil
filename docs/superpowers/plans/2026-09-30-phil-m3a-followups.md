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
  backend root and skips write permissions. The light harness (`phil.agents.factory`), which
  the answerer uses, turns this off; the deep agents reading snapshots -- the architect and
  /btw -- may still have it enabled, writing into their snapshot. Check their middleware
  stacks and disable eviction there too, since a write into the backend root defeats
  "read-only."
- **The run's depth is recorded only in the chat transcript's `route` event note.** Spec
  §3.5 wants it on the run itself. This needs a `runs` table migration (a `depth` column)
  and a `phil show` line printing it; natural to pick up in M3b alongside the quick engine.
- **A forced `/ask` diagnosis never offers a fix**, and a bare `/full` at the fix prompt
  (spec §3.6: "Fix it? (Enter = quick fix, /full = plan it)") prints the command's usage
  instead of accepting it, because `/full` is currently only recognised as a routing
  override on a new goal, not as an answer to the fix prompt. Spec §3.6's "/full = plan it"
  needs its own handling at that prompt; natural for M3b, alongside the quick path it would
  start.
- **`controller.py` is about 1530 lines.** The routing (`_route_job`/`_on_route_ready`/
  `_routed`/`_status_line`) and answer (`_answer_job`/...) methods could move into a helper
  module (e.g. `phil/chat/routing.py`) to keep the controller focused on orchestration.
- **`test_route_state`'s file count depends on the autouse `gitconfig` fixture** writing
  into `tmp_path` (it adds a tracked file that `route_state`'s `git ls-files` then counts).
  The test passes today, but it's coupled to a fixture it doesn't name; worth asserting
  against the fixture's own file list instead of a hardcoded count, so a change to the
  fixture doesn't silently break an unrelated test.

## From the final review

- **Setup ignores a classifier set in the repo's `phil.toml`** (it offers no Keep option for
  it), so rerunning setup can suggest a classifier the repo already overrides.
- **The benchmark's sweep doesn't yet report the cost of fall-throughs to intake.** A
  message deferred to intake costs an intake call that a confident route would have saved;
  the sweep table shows the intake rate but not that cost.
- **The answerer's hard stop bounds one question at about 28 model calls**
  (2 × (12 + 2): two contract attempts, each the 12-call cap plus 2 grace calls). State
  this in M3b's spec update.

## User decisions

- Setup holds entered keys until it finishes; cancel saves nothing (user decision 2026-10-01).
