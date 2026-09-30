# Plan 6a follow-ups

Deferred items from implementing plan 6a ("stop the waste"). Plan:
`2026-09-29-phil-06a-stop-the-waste.md`. Spec:
`../specs/2026-09-29-phil-06a-stop-the-waste-design.md`. Ledger:
`.superpowers/sdd/2026-09-29-phil-06a-stop-the-waste/progress.md`.

## Benchmark harness (Task 1)

- The usage query's `WHERE` clause duplicates `chat_usage`'s; consider sharing one query.
- Intake isn't run in the benchmark (the case's goal goes straight to the architect), so
  intake's calls are never counted in a benchmark record.
- The benchmark doesn't mirror the chat's freezing of the test command at approval.
- No benchmark case covers test-command detection or the no-test-suite path.
- A check task runs both the test command and its `check_cmd` on every verify, so a site whose
  tests also build does the build twice; no benchmark case measures that cost.
- The benchmark's test helpers are imported from a conftest rather than a shared helpers module.

## Shell policy and evidence (Tasks 2 and 3)

- The test command's trailing arguments reach pytest's `--basetemp`/`--rootdir` unchecked —
  accepted, since the test command is already arbitrary code execution.
- Evidence accepts any claim that starts with a called tool's name; it doesn't check that the
  claim's content matches what the tool actually returned.
- `RunEngine._context` re-parses the plan on every call instead of caching it.
- `git branch --contains X` / `--merged X` are refused. Fails closed (a usability gap, not a
  leak) because the grammar can't yet tell a revision argument from a mutating one.
- A glob character in an approved command's program token (`argv[0]`) never matches a literal
  `argv[0]` — again fails closed.
- The grep/rg file-taking-option tables are a snapshot of rg 15.2 and GNU/BSD grep; a future
  version's new option needs adding by hand.
- A grep/rg pattern is over-refused as an outside path when its position in the argument list is
  ambiguous (for example a positional pattern that looks like a path).

## Gates and test output

- Only pytest output is parsed (`parse_failures`/`parse_counts` in `src/phil/run/gates.py`). With
  another runner, a baseline that already fails makes red impossible (every failure reads as
  `exit code N`, which the baseline already holds), and green's added-tests check is skipped (no
  counts). Detection now reaches `npm test`, `go test` and `cargo test` more often, so this bites
  more runs.

## Worklogs and attempts (Task 5)

- The implementer's diff is built fully in memory before the size cap is applied.
- The first green-phase call carries the red phase's diff — brief-mandated, not a bug.
- A test imports across a module boundary it shouldn't need to.

## Test-command detection and resume (Task 6)

- Runs launched before Task 6 froze `phil.toml`'s command into `plan.test_cmd`/`config_test_cmd`
  at launch, so they never pick up a later config switch on resume.
- The `_detection_root` failure-logging path has no test.
- The `test_cmd_changed` event can be re-posted if a worker dies mid-rebaseline and the run is
  resumed again.

## M3 — proportional orchestration

Deferred by the spec's scope line, to be designed from M1's benchmark numbers:

- Goal-size and task-class routing (a classifier that chooses process depth and model tier).
- Lighter agents for small work: no sub-agent or summarization.
- Review findings patched directly instead of becoming new TDD tasks.
- Tighter retry limits.
- The larger prompt and handoff redesign.
