# Plan 2 (Agent Core): Follow-ups for Later Plans

Findings from plan 2's task reviews and final review that were deliberately deferred. Plan 1's remaining items are in `2026-09-23-phil-01-followups.md`.

## Before merging plan 2

- **Run the live test yourself.** It is the only check against a real provider. It needs `OPENROUTER_API_KEY`, which agents must not read:

      set -a; source .env; set +a
      uv run pytest -m live -v

## Plan 3 (run graph): design inputs

- **Shell approval.** deepagents 0.5.6 has no `"interrupt"` permission mode (only `"allow"`/`"deny"`), and `run_shell` is a plain closure. Calling LangGraph `interrupt()` inside the tool would re-run the whole run-graph node, including the agent call and its worktree changes, on resume. Simpler v1: `run_shell` records denied commands in `CommandLog`, the agent finishes, and the node escalates afterwards to request approval and a re-run. Decide explicitly.
- **Validate `Plan.test_cmd` against `ShellPolicy` at approval.** An architect-proposed command outside the allowlist (e.g. `python -m pytest`) would fail every implement call's evidence check.
- **Code gate after the tester.** The tester can write files, and "do not fix product code" is only in its prompt. Reuse the red-gate rule (only `test_globs` paths may change).
- **Budgets.** `RoleBudget` defaults to 12k tokens for every role, and the tester/reviewer receive the full diff inside the untrimmable contract JSON, so `PacketTooLarge` will appear on modest runs. Raise per-role defaults or pass the diff as a file reference.
- **Usage and caps.** `extract_usage` sums only the main agent's messages; deepagents adds a general-purpose `task` subagent whose usage never reaches them, and it sets `recursion_limit=9999`. Collect usage with a callback handler inside the factory, add a model-call or recursion limit, and decide whether to disable the general-purpose subagent.
- **Graph-test fakes.** `FakeAgentFactory` shares one agent across roles; graph tests need scripted outputs per role (e.g. a dict of role → `FakeAgent`).
- **Migrations.** `_statements` splits on bare `;`; switch to accumulating with `sqlite3.complete_statement` before the first migration with triggers or string literals, and add a rollback test.
- **Retries.** A retried agent call starts on a worktree it may already have changed; failed-then-retried provider calls get no telemetry row; `Retry-After` is ignored.
- **Prompts.** Tell the implementer and tester what to do on `DENIED` (do not try variants; record it in `self_check.unverified`). Tell the architect to follow the repository's conventions and choose a `test_cmd` that fits the allowlist.
- **Log access.** The "full log:" path in `run_shell` output is outside the agent's virtual root; say it is for humans or add a log-reading tool.
- **Live coverage.** Add a live test that forces an invalid first answer, to confirm providers accept the retry's two consecutive user messages and strict mode accepts `pattern`/`max_length`.

## Plan 4 (chat layer)

- **Architect needs a real workdir.** With `workdir=None` the backend is in-memory, so the architect cannot read the repository. Pass the repo root as `workdir` (read-only permissions then apply to the user's real checkout).
- **Deny reads of secret files in the user's checkout.** Unlike a worktree, it contains untracked files such as `.env`. Add read-deny rules for `/.env*` and `/**/.env*` (and consider other secret patterns) for chat-layer roles.
- **Planning assumptions.** With `artifacts=None`, `invoke_agent` drops assumptions; persist planning-stage assumptions so the reviewer sees them.
- **Parked item sources.** Items parked without an `ArtifactStore` get `Ref(path="")`; give them a meaningful source.
- **Terse limits.** Only `TaskResult.summary` has a `max_length`; add limits to notes and issue text when `Brief` is built.
