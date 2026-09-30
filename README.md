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

## Pull requests and cleanup

Needs the `gh` CLI installed and logged in (`gh auth login`) and an `origin` remote. Without
either, `phil pr` and `phil clean --merged` say what's missing and exit 1; the chat's own
checks just skip.

When a run completes, the chat asks `Open a PR for <run> → <base>?`. Answer `y` to push
`phil/<run-id>` and open the pull request; anything else declines (printing `phil pr <run>` as
the way to open one later) and, unless it's blank/`n`/`no`, is kept and used to start the next
goal. Open a PR for any completed run directly, any time, with:

    phil pr <run-id> [--base <branch>]

The PR body lays out any action still needed, what changed (the plan's description and tasks),
and how it was verified: a tests line (no new failures against the base, or how many are still
failing), the number of tester reports, and the newest reviewer verdict. If the repo has a PR
template, it is appended under `## Template`, unfilled.

The chat notices a merge on its own — each run's open PR is checked at most once every 5
minutes — and `phil runs` / `phil show <run>` check too whenever you run them. A merge prints
`<run> merged (#N); cleaned up.`; a PR closed without merging gets one notice and needs
`phil clean <run>` to remove it; a PR closed and later reopened isn't checked again. A merge is
only cleaned up if the PR's head is still the run's branch, and the remote branch is deleted
only while it still points at the PR's head commit (otherwise it's left alone, with a warning).
A check that fails (gh offline, not logged in, a surprise) is never printed and is retried next
time; it's logged to the `phil` logger, which the chat writes to its `phil.log` — the plain CLI
commands have no log file, so there it goes nowhere.

`phil clean <run>` removes the run's worktree, local branch, checkpoints, and scratch files,
keeping `summary.md`, `open_issues.json`, and the run's telemetry rows in `phil.db` (`--purge`
also removes `summary.md`/`open_issues.json`). It leaves the remote branch alone — that's only
deleted after a merge, whether the chat noticed it or you ran:

    phil clean --merged      # clean up every run whose pull request has merged

A merge cleanup also appends one entry to `~/.phil/projects/<slug>/learnings.md` (created on
first use): the run's goal, confirmed/unconfirmed assumptions, and up to 5 reviewer notes in
the same `- (severity) note [file:line]` format `phil show` uses for open issues. Nothing in
Phil reads this file yet — it's a plain log for you.

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

## Shell commands

Read-only commands run without needing a `[shell] allow` entry: `cat`, `find`, `git branch`,
`git diff`, `git log`, `git ls-files`, `git show`, `git status`, `grep`, `head`, `ls`, `pwd`,
`rg`, `tail`, `wc` — as long as every path they touch stays inside the run's worktree. A flag
that would let one of these write, delete, run something, follow a symlink out of the worktree,
or read a list of paths from a file is refused (`find -exec`, `find -files0-from`,
`git branch -D`, `grep -R`, `rg --pre`, `ls -L`, `wc --files0-from`, and similar), as is any command
using shell operators (`;`, `&&`, `|`, backticks, redirects, newlines).

Everything else needs an entry in `[shell] allow` — matched exactly, or with a trailing `*` in
the pattern to also allow trailing arguments (e.g. `pytest *`) — or a live approval after a
denial. A command off both says
`DENIED: ... is not on the allowlist`; one that can never run here — a shell operator, a
containment escape, a mutating flag on an otherwise read-only command — says
`REFUSED: ... and can never run here`, and Phil doesn't retry variations of it.

The plan's approved test command and every task's `check_cmd` run for that run without needing
an allowlist entry of their own (exact match plus trailing arguments) — they were shown in the
plan you approved.

```toml
[shell]
allow = ["pytest", "pytest *", "uv run pytest", "uv run pytest *", "npm test", "git status", "git diff", "git diff *"]
timeout_s = 300
max_output_lines = 200
```

## Plans

Every task is `tdd` (the default) or `check`. A `tdd` task is driven by a failing test: red,
then green. A `check` task is for a change with no testable behaviour — copy, markup, static
assets, config, docs — and skips the red phase entirely; it's verified by its `check_cmd`, a
single shell command (no pipes, `&&`, or redirects) that the plan shows you and that must exit 0
for the task to pass. If every task in a plan is `check`, the plan needs no test command.

## Test command

Phil looks for the project's test command in order: the plan's own `test_cmd`, then `phil.toml`'s
`[project] test_cmd`, then detection from the repo's files — a `package.json` with a real `test`
script gives `npm test`; a `pyproject.toml`, `pytest.ini`, or `conftest.py` gives `uv run pytest`
(or plain `pytest` without a `uv.lock`); `go.mod` gives `go test ./...`; `Cargo.toml` gives
`cargo test`. If none of these apply and the plan has a `tdd` task, the chat won't start the run.

A paused run picks up a change to `phil.toml`'s `[project] test_cmd` on resume, but only when
that value has actually changed since the run started — an approved plan command that still
differs from an unchanged `phil.toml` is left alone. Picking up the change re-baselines the run's
test results against the worktree's current `HEAD`, and the chat (and `phil attach`) print
`Using the updated test command: ...`.

## Benchmark

An opt-in live benchmark (`tests/live/bench`) plans and runs a few small goals — add a Python
function, add a meta tag, fix a typo — with real models, through Phil's real chat and run code
paths, and appends one JSON record per case to `~/.phil/bench/results.jsonl` (or
`$PHIL_BENCH_RESULTS`):

    PHIL_BENCH_CONFIG=~/bench.toml uv run pytest -m bench -n 0
    uv run python -m tests.live.bench.report

`PHIL_BENCH_CONFIG` points at a `phil.toml` that sets the `[models]` to benchmark. Always add
`-n 0` when comparing timings: the default `-n auto` runs cases in parallel, and the contention
inflates each case's `minutes`. The report prints each case's last 5 runs (`--last N` for more)
with its task count and modes, state, pass/fail, minutes, calls, model calls, tokens, and cost.

To compare against a baseline, run the benchmark from a separate `git worktree` pinned at the
commit you're comparing from (for plan 6a, the benchmark's own baseline commit, `50fea6d`), so
later commits on your branch can't leak into the baseline numbers (each record carries the short
`phil_sha` it ran):

    git worktree add ../phil-bench-baseline 50fea6d
    cd ../phil-bench-baseline
    PHIL_BENCH_CONFIG=~/bench.toml uv run pytest -m bench -n 0

Then run the same command again from the branch under test, and compare the two
`uv run python -m tests.live.bench.report` outputs (tokens, calls, minutes, and pass rate).

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
