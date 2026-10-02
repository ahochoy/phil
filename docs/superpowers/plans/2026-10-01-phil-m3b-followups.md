# Plan M3b follow-ups

Deferred items from implementing plan M3b ("quick path"). Plan:
`2026-10-01-phil-m3b-quick-path.md`. Spec:
`../specs/2026-09-30-phil-m3-proportional-orchestration-design.md`. Ledger:
`.superpowers/sdd/2026-10-01-phil-m3b-quick-path/progress.md`.

## From the task 7 brief

- **The review's fix-after-review patch can't touch test files outside the task's own**
  (Ruling R3, task 3). Broadening this — letting a fix edit any test the review names — needs
  a way to tell "this task's tests" from "someone else's coverage" once a quick task's reach
  is more than one file; today it's `patch_editable_tests`, scoped to what the task itself
  changed.
- **The quick task's `check_cmd` comes entirely from intake's judgement** (M1's rule: prefer a
  command the repo already defines). Nothing validates that choice beyond
  `launch_problems`' shell-safety check (forbidden operators, out-of-tree paths) — there's no
  check that the command intake picked actually exercises the change. The benchmark's two
  `check`-mode site cases exercise real choices, but only for one fixture's shape
  (`node build.mjs`); watch what intake actually proposes once this runs against real models.
- **Revisit the per-attempt cap of 15** (`QUICK_IMPLEMENTER_MAX_MODEL_CALLS`,
  `phil.agents.registry`) against this benchmark's live numbers, once `PHIL_BENCH_CONFIG` is
  pointed at real models. The target is under 10 model calls total for a quick case (spec
  §5.2); 15 per attempt is a per-implementer ceiling, not tuned against that target yet.
- **The Jev decision-rule revisit after M3** (carried from memory, M3a follow-ups): once both
  M3a and M3b are live-benchmarked,
  - split the decision rule's thresholds per backend rather than one fixed bar for all of
    them;
  - count a deferral (routed to intake) apart from a wrong path (routed, but to the wrong
    depth) — a deferral costs an intake call, a wrong path costs a bad run;
  - allow a tolerance of at least one mismatched case in the small (~40-request) classifier
    set before calling a backend worse, since one case is noise at that sample size;
  - check case `f-01`'s label in `tests/live/bench/classify/cases.jsonl` — it was flagged as
    possibly mislabelled when the rule was first written and was never rechecked.

## From the task reviews (tasks 1–6)

- **A write through a symlink already in the repo, pointing at `.git`, reaches `.git`** — true
  of every writer harness (the light implementer, the deep architect/btw, the answerer), not
  just the quick implementer. The `[filesystem_permissions]` deny list matches literal and
  case-variant paths, not a resolved target. Consider a backend wrapper that checks the
  casefolded *resolved* real path before any write, rather than pattern-matching the path as
  given (task 2's final review; the `[shell]` allowlist is a wider route to the same risk, by
  design, so this is specifically about the filesystem middleware).
- **The quick review's fix may weaken assertions in the task's own tests**, not just add to
  them (Ruling R3, task 3, labelled "unverified" at review time — the reviewer saw the
  fix-after-review path once and it didn't regress the suite, but nothing stops a future fix
  from loosening an assertion the original task wrote).
- **The patch logic (`patch_editable_tests`, `_abandon_patch`, and the quick engine's
  fix-after-review step generally) assumes one-task quick plans.** That's true today (a quick
  plan is always exactly one task), but it's an invariant enforced by convention, not by a
  type or a check — worth a guard, or a comment at the one place that could grow a second
  task, if the quick plan ever stops being one task.
- **Intake's `detected_test_cmd` hint reads the live root, while the run (and its
  `launch_problems` check) reads the base-commit snapshot** (task 5's review). This benchmark
  mirrors that exact mismatch (`detect_test_cmd(info.root)` in `tests/live/bench/harness.py`,
  matching `ChatController._intake_job`) rather than fixing it, since fixing the chat's
  behaviour is out of this task's scope — but a repo whose live tree and base commit disagree
  on a test command could get an intake hint that doesn't match what the run detects.
- **`_full_handoff` and `_prior_attempt` aren't persisted across a chat reopen** (task 6):
  both live only in `ChatController`'s in-memory state. Reopening a chat mid-escalation (or
  right after one) loses the "move to full" option's prior-attempt worklogs; the escalation
  itself is still on the run row, so nothing is lost permanently, but the handoff's extra
  context would need to be re-derived from `handoff/prior_attempt.json` on reopen rather than
  from the controller's memory.
- **Moving to full is keyed on the run's `aborted` state, not on a dedicated
  `moved_to_full` flag**, even though `RunState` already carries `moved_to_full` (task 6).
  Any future "aborted for another reason" path would need to be told apart from "aborted
  because the user chose full" by something other than state alone; today only the controller
  reads `moved_to_full` for that, and other state readers still branch on `aborted`.
- **There's no test driving the full engine (not a scripted stand-in) through an actual
  handoff** — reading `handoff/prior_attempt.json` back into a real `ArchitectInput.prior_attempt`
  end to end (task 6). The existing tests check that the file is written, and that
  `ArchitectInput` accepts the field; nothing exercises both halves together against the real
  engine.
- **Lint wasn't run on tasks 5–7's changes: `ruff` isn't installed in this venv**
  (`uv run ruff` fails with "No such file or directory"). Worth adding it to the project's
  dev dependencies, or documenting that lint runs from a separate environment, so it isn't
  silently skipped task after task.

## From a live benchmark run

- **A test or check command that can't be found (exit 127), e.g. `npm` not on the background
  worker's PATH under nvm, passes quietly because the baseline failed the same way.** Phil
  should stop the run with a clear message, and make sure the worker inherits the user's PATH.
- **The quick implementer used 8 model calls on a one-line docs change and 17 total on
  py-multiply, against a target of under 10**: try a leaner quick-implementer prompt or a
  tighter cap.
- **Provider flakiness: OpenRouter's DeepSeek returned stub goals for a while**; the objective
  guard now rejects them with a retry.

## From this task (7)

- **The benchmark's "full, or intake deciding" branch skips intake entirely**, even when the
  router leaves the depth to intake (spec §5.2 reads "full, or intake deciding: today's
  architect path" as one branch, and the harness implements it that way: `Goal(objective=case.goal)`
  goes straight to the architect). The live chat is less decisive here — it always calls
  intake, and lets intake's own `goal.depth` choose quick when the router deferred. A case
  whose router confidence is low but whose intake call would have chosen `quick` is
  indistinguishable, in this benchmark, from one the router sent straight to `full`. Revisit
  if a benchmark case ever needs to probe that specific chat behaviour.
- **`explain-module`'s fixture deviates from the brief's placeholder path.** The brief named
  `calc.py` and a `divide` function; the fixture's module is a package
  (`calc/__init__.py`), and `divide` didn't exist, so `expect_file` was set to
  `"calc/__init__.py"` and the fixture gained a `divide(a, b)` raising `ZeroDivisionError` on
  a zero divisor — per the brief's own instruction to adjust after reading the fixture.
- **The answer case's "no new commits" check is tied to `_init_repo`'s exact last commit
  message** (`"Configure phil"`, in `tests/live/bench/cases.py`'s `_explain_module`). It's
  accurate today, but it's coupled to a string in a different file rather than to, say, a
  commit count captured at repo-init time; a change to `_init_repo`'s commit sequence would
  silently stop catching an unwanted commit from the answer path.
