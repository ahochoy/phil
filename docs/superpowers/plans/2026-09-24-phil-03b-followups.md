# Plan 3b (Worker and CLI): Follow-ups for Later Plans

Findings from plan 3b's task reviews and final review that were deliberately deferred. Earlier follow-ups: `2026-09-23-phil-01-followups.md`, `2026-09-23-phil-02-followups.md`, `2026-09-24-phil-03a-followups.md`.

## Design rules learned in 3b (apply to every later plan)

- **One worker per run.** A worker takes a run only through `claim_run` (one `UPDATE` guarded by the row's pid and the transition table) and clears its pid only through `release_run` (owner-only). Anything that spawns a worker (`resume`, `attach`, later the chat layer) must also check `worker_starting(run_events(...))`, because a just-spawned worker has not written the row yet.
- **A checkpointed pause wins.** If the graph checkpoint holds an interrupt, `continue`/`start` re-record the pause instead of refusing, so a pause lost to a crash or stop is never a dead end.

## Plan 4 (chat layer)

Carried from plan 3a:

- Usage callback (subagent tokens are not counted, so the budget guard is lenient).
- OpenRouter SDK timeout.
- 200-with-error responses treated as transient.
- Lean read-only architect harness.
- Validate the architect's `Plan.test_cmd` and show it at plan approval, with the one-line note when `[git] sign_commits` is not false or `run_hooks` is true.
- `open_issues` deduplication across tester and review rounds; clean up raw newlines and markdown in `render_summary` when the summary becomes a `Brief`.

## Before a public release

- **Process-identity liveness check.** `is_worker_alive` trusts the pid plus a 30s heartbeat window. After a laptop sleep the heartbeat is stale while the worker is fine, and within the window a reused pid reads as alive. Match `_worker <run_id>` in the process's argv (e.g. `ps -o args= -p <pid>`) as well.
- **Gate `PHIL_AGENT_FACTORY` behind a test-only switch.** Today any environment can point the worker at an arbitrary `module:callable`.

## Later

- `attach`'s wait loop samples the row state; a fast re-pause is only told apart from "worker did not start" by the spawned worker's `worker` event once the process has exited. Wait for the `worker` event with the spawned pid instead of sampling the row.
- `attach`'s exit code ignores the outcome (it returns 0 for a failed or stopped run).
- With no `escalation` event, `attach` offers only `abort` while `resume` offers nothing; make them agree.
- `attach` uses `assert` for control flow (`get_run(...)` not None).
- `--hint` is silently ignored for actions other than `retry`.
- `spawn_worker` writes the `spawn` event after `Popen` without a guard; a failed append leaves a live worker with no `spawn` event.
- `clean` removes the worktree before the branch check; a failure after that point needs a re-run (it is re-runnable).
- `clean` force-removes a stopped run's uncommitted changes (accepted: the engine discards partial phase work on resume anyway).
- A `killpg` `PermissionError` in `kill_active_groups` could shadow the original exception in the worker's handlers.
- Worker cleanup isolation is untested, and the handler blocks catch `BaseException`.
- Tests monkeypatch `Popen` / `os.kill` process-wide.
- Run-state transition checks are read-then-write across processes; only the worker's claim is atomic.
