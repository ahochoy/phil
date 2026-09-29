# Phil

Phil is a CLI coding agent built around explicit contracts between agents, managed context, and code-enforced gates. Run it inside a git repository; it plans a goal with you, then implements it in an isolated git worktree and leaves a tested, reviewed branch.

Design: `docs/superpowers/specs/2026-09-23-phil-v1-design.md`

## Usage

Plan and start work from a chat in your repo (set models first — see phil.toml):

    cd your-repo
    phil                     # type a goal; approve the plan with y / edit / n

One chat follows one goal at a time — intake, plan, approval, then its run — and shows a live
bottom toolbar (the current step and its elapsed time, then the run's node and task progress).
The prompt stays usable while a goal is being planned or a run works in the background:

- If the run needs your input, the chat flags it right away (`⏸ <run> needs you: …`) and asks
  at the next idle prompt; jump to it any time with `/answer`.
- Ask a side question while work continues with `/btw <question>` — read-only, it never changes
  the plan or the run; its answer can reference files, opened by number with `/more <n>` (only
  from that answer's read-only repo snapshot — never the live working tree).
- If a run failed or was stopped, continue it with `/resume`.
- `/show` prints the chat's run: tasks, a usage table, open issues, and numbered details.
  `/more <n>` expands one detail from the most recent listing (`/show`, a `/btw` answer, or the
  run's completion notice) — the full text of a log, packet, or agent output.
- `/park <note>` sets an idea aside instead of acting on it now; `phil parked` lists the parking
  lot, and its open count shows at chat start and in the toolbar.
- The toolbar shows the chat's running cost (its own agent calls plus its runs'), e.g. `$0.42`
  reported by the provider or `~$0.42` estimated from a cached price list (see Observability).
- `/runs` lists runs, `/help` shows the commands, `/quit` (or Ctrl-D) leaves the chat — a run
  left running keeps going in the background.

Reopen a chat later:

    phil                      # lists this repo's open chats; pick a number or press Enter for a new one
    phil --resume <chat-id>   # reopen a specific chat directly
    phil --new                # skip the list and start a new chat

## Observability

Every model call is counted, including calls made inside a deep agent's own sub-agents — not
just the top-level ones. `phil runs` shows accurate tokens and cost per run; `phil show <run>`
breaks it down by layer and role (calls, model calls, tokens in/out, cost, tool calls, retries),
lists open issues, and numbers the run's details (worker log, test logs, reviewer/tester output,
packets); `phil show <run> <n>` prints one detail in full (`/more <n>` does the same in the chat).

Cost markers, wherever a cost is shown:

- `$0.42` — the provider reported the cost.
- `~$0.42` — no reported cost; computed from tokens × the model's published price.
- `$0.42?` or `$?` — some part of the cost couldn't be priced (a call's model isn't in the price
  list): `$?` when the computed total is exactly zero, `?` appended to the formatted amount when
  there's a nonzero total from other calls alongside the unpriced one.

Estimated costs come from OpenRouter's public price list (`GET
https://openrouter.ai/api/v1/models`, no key needed), cached under `~/.phil/cache/` for a day.
Only `openrouter:<model>` models are priced this way; a model configured under a different
provider prefix always shows as reported or unknown, never estimated.

A run pauses at 100% of its `[run] max_tokens` / `max_cost_usd` limits as before, and now warns
once at `[run] warn_at` (default `0.8`, i.e. 80%) of whichever limit it's closer to; the chat and
`phil attach` print the warning (e.g. `r-7f3a has used 80% of its budget ($1.61 of $2.00).`).
Because every model call now counts, including a deep agent's sub-agent and summarisation calls,
the token limit defaults to `1500000`; cost (`max_cost_usd`, default `2.0`) is the primary guard.

Model calls time out after `[run] model_timeout_s` (default `180`) and Phil's own retry policy
runs instead of the provider SDK's (SDK retries are off for OpenRouter, OpenAI, Anthropic and
Google). An agent's model calls — including a deep agent's sub-agent calls — are retried one
at a time, so a transient failure on the tenth call repeats just that call, not the nine before
it or the tools they ran. The exception is the summary call deepagents makes when it compacts a
long conversation: a transient error there re-runs that agent step from the start. A timeout gets
2 tries total (a stuck provider otherwise costs one full timeout per attempt); other transient
errors — rate limits, 5xx, Anthropic's 529 "overloaded", connection failures, a 200 response
carrying a transient error code — get 3. Every retry is counted in the `retries` column of
`phil show`.

```toml
[run]
model_timeout_s = 180
warn_at = 0.8
max_tokens = 1500000
max_cost_usd = 2.0
```

## Development

    uv sync
    uv run pytest
    uv run phil --version

To use `phil` from any repo, install it as an editable tool. Reinstall after dependencies change (code changes are picked up automatically):

    uv tool install --editable ~/Code/phil --force

The original LangGraph prototype is kept in `prototype/` for reference and is not part of the package.

Live tests call a real model through OpenRouter and are skipped by default:

    set -a; source .env; set +a
    uv run pytest -m live

Tests run in parallel with pytest-xdist; use `uv run pytest -n 0` to run serially when debugging.

Start a run from a plan file and follow it:

    uv run phil run plan.json
    uv run phil attach <run-id>
