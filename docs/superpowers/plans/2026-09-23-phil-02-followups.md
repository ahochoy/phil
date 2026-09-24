# Plan 2 (Agent Core): Follow-ups for Later Plans

Findings from plan 2's task reviews and final review that were deliberately deferred. Plan 1's remaining items are in `2026-09-23-phil-01-followups.md`.

## Before merging plan 2

- **Run the live test yourself.** It is the only check against a real provider. It needs `OPENROUTER_API_KEY`, which agents must not read:

      set -a; source .env; set +a
      uv run pytest -m live -v

## Findings from the first live runs (2026-09-23)

- **Free model IDs disappear.** `poolside/laguna-m.1:free` was removed from OpenRouter (404 "No endpoints found"). The default is now `nex-agi/nex-n2.5-pro:free`, the only free model that passed both the lean critic and the deep architect probe. `poolside/laguna-s-2.1:free` passed the architect in 15 s but its critic was never verified because of rate limits; it is a good per-role override candidate.
- **Provider tool-schema limits.** ModelRun (host of `qwen/qwen3.8-27b:free`) rejects deepagents' `grep` tool because its `path: str | None` schema is ambiguous to ModelRun's grammar compiler. Judging roles now use the lean harness, which avoids it; deep roles still depend on the provider.
- **The deep harness is expensive.** Planning one function in a two-file repo cost the architect 35k–54k input tokens under deepagents, versus about 1.5k for the lean critic. Plan 3 should give the architect a lean, read-only harness (read/ls/glob tools only, no planning or subagent middleware) and measure the difference with telemetry.
- **The OpenRouter SDK retries 5xx for up to an hour** by default (`BackoffStrategy(500, 60000, 1.5, 3600000)`), so one call can hang silently. Set an explicit timeout and retry policy when building the model in the factory.
- **"Overloaded" can arrive as HTTP 200.** nemotron returned a 200 whose body carried a `provider_overloaded` error, which the SDK raised as `ResponseValidationError`. `is_transient` does not recognize it; classify it as transient.
- **Rate limits dominate free-tier runs.** Four of twelve probe runs failed on 429s alone. The probe script used for model selection is worth keeping as a `phil doctor`-style command later.

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
