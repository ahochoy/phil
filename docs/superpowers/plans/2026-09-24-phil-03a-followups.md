# Plan 3a (Run Engine): Follow-ups for Later Plans

Findings from plan 3a's task reviews and final review that were deliberately deferred. Earlier follow-ups: `2026-09-23-phil-01-followups.md`, `2026-09-23-phil-02-followups.md`.

## Design rules learned in 3a (apply to every later plan)

- **LangGraph re-runs an interrupted or crashed node from the top.** Every node with side effects must be safe to re-run: `implement` and the tester reset the worktree on entry, `commit` skips when nothing changed, and `escalate` loops on invalid answers instead of raising (a raised error after `interrupt()` makes LangGraph replay the stale answer forever).
- **Gates must not trust a test run that did not run.** Tests run with `--continue-on-collection-errors` (via `PYTEST_ADDOPTS`), and red/green compare pytest's passed/skipped counts. Non-pytest runners only get the weaker "new failures" check.

## Plan 3b (worker and CLI) — must handle

- **Exceptions escape `runner.start`/`resume`/`continue_run`.** The worker must catch them, set the run row to `failed` or `needs_attention`, and leave recovery to `continue_run`. Enforce valid run-state transitions in `update_run` (plan-1 follow-up).
- **Start once per thread.** Invoking with an initial state on a thread that already has a checkpoint restarts from START; check `graph.get_state(config)` and use `continue_run`. Check for a pending interrupt before `resume`.
- **Stop must kill child process groups.** `run_command` uses `start_new_session=True`, so killing the worker leaves pytest and agent shell commands running; track and kill their process groups.
- **Connections.** The engine connection and the checkpointer connection (`check_same_thread=False`, never closed) share `phil.db`. A heartbeat thread needs its own connection; close the checkpointer on exit; `phil clean` must call `checkpointer.delete_thread(run_id)` and delete any `refs/phil/<run>` refs.
- **Pin the red snapshot.** `red_tree` is an unreferenced git tree; `git gc --prune=now` in the user's repo can delete it while a run sits escalated. Pin it with `git update-ref refs/phil/<run>/red <tree>`.
- **Resume inputs.** `RunDeps.worktree` and `repo_root` must come from the run row; detect a removed worktree before resuming.
- **Escalation payload for `attach`.** Payloads carry `reason`, `options`, `summary`, `problems` / `commands` / `resume_to`, and `error` after an invalid answer. Add log paths for the last test reports (spec §7 "last 3 reports").
- **Suite speed.** The suite takes about 2 minutes because engine tests run real pytest. Add `pytest-xdist` (tests are isolated by `phil_home`) before adding more engine tests.
- **Tester refused commands are not reported.** `_run_tester` notes only `log.denied`; commands in `log.refused` (shell operators, risky flags) never reach `open_issues`. Add them as notes.
- Carried from plans 1–2: usage callback (subagent tokens are not counted, so the budget guard is lenient), OpenRouter SDK timeout, 200-with-error responses as transient, lean read-only architect harness.

## Plan 4 (chat layer)

- Validate the architect's `Plan.test_cmd` and show it at approval.
- `open_issues` are not deduplicated across tester and review rounds; `render_summary` prints raw newlines and markdown from notes. Clean both up when the summary becomes a `Brief`.

## Later

- `pyproject.toml` `addopts` can still weaken tests during green (pyproject is not a test path because green legitimately edits it). The pass/skip count checks catch most cases; the reviewer is the backstop.
- Red may edit test-path helpers that product code imports (path-only heuristic).
- `changed_files` lacks `-z`, so non-ASCII paths are misread (plan-1 follow-up).
- More than 999 tasks in one plan crashes id generation; `model_copy` skips the Plan id validator.
- A crash between the tester's commit and its checkpoint can produce a second "tests from tester" commit on resume.
- `implement` reuses `call_seq` after a crash, overwriting that call's artifacts and duplicating telemetry `call` rows.
- The run row's `tasks_done`/`tasks_total` lag after a skip or new fix tasks until the next commit.
