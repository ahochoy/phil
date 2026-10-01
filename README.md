# Phil

Phil is a CLI coding agent built around explicit contracts between agents, managed context, and code-enforced gates. Run it inside a git repository; it plans a goal with you, then implements it in an isolated git worktree and leaves a tested, reviewed branch.

Design: `docs/superpowers/specs/2026-09-23-phil-v1-design.md`

## Getting started

The first time you run `phil` in a terminal, in a repo with no models configured, setup
starts automatically:

    cd your-repo
    phil

It walks you through choosing a provider, saving its API key, picking a `high` and `low`
model (with suggestions, where there are any — see below), and checking that each model
actually answers, then writes `~/.phil/config.toml` and starts the chat. Run it again any
time to change providers or models:

    phil setup

Setup only ever edits `~/.phil/config.toml`, never a repo's `phil.toml`. It keeps your other
settings and comments, prefilling what it can from the current global file. Ctrl-C or Ctrl-D
cancel at any step and save nothing — no config, no key. Keys you enter are saved to the
keychain only when setup finishes; an empty answer at the key prompt doesn't cancel, it just
skips entering one, and setup carries on and writes your model choices.
A key pasted by mistake at any other prompt is refused without being shown or saved.

Suggested `high`/`low` models exist for OpenRouter, OpenAI and Anthropic; Google has none yet
— setup asks you to type a model id by hand. Ollama offers whatever you have installed, and a
custom OpenAI-compatible provider offers whatever it serves. The `classifier` tier isn't part
of setup yet and has no suggestion; set it by hand under `[models]` until it's wired up in M3.

    phil keys set <provider>      # store a provider's API key in the OS keychain
    phil keys list                # where each provider's key would come from: env or keychain
    phil keys remove <provider>   # delete a provider's stored key

A key is always read from its environment variable first; only when that variable isn't set
does Phil fall back to a key stored in the keychain. So an exported variable always wins over
a stored one. When no keychain is available here (containers, some CI, headless boxes), both
`phil setup` and `phil keys set` tell you to export the variable instead of trying to store it
— see Configuration → Keys below for the full rule.

## Configuration

Settings resolve in layers, each overriding the last: built-in defaults, then your global
`~/.phil/config.toml`, then the repo's `phil.toml`, then any `--set` on the command line.
Tables (`[run]`, `[models]`, `[providers.x]`, …) merge key by key; a plain value or list
replaces whatever a lower layer set.

    phil config          # the effective settings, each line tagged with the layer that set it
    phil config --path   # where the global and repo files are, and whether they exist

`--set key.path=value` (repeatable) overrides one setting for a single invocation — on the
chat, `phil run`, `phil config` and `phil models check` only:

    phil --set models.low=ollama:qwen3:8b
    phil run plan.json --set run.max_cost_usd=5

A minimal global file picks a strong model (`high`) and a light one (`low`), and needs one
environment variable for its key:

```toml
# ~/.phil/config.toml
[models]
high = "openrouter:openai/gpt-6-luna"
low = "openrouter:openai/gpt-6-sol"
```

    export OPENROUTER_API_KEY=...

A repo's `phil.toml` then holds only project settings — it doesn't need to repeat models at
all:

```toml
# phil.toml
[project]
test_cmd = "uv run pytest"
```

API keys are never read from or written to a config file, only resolved by name: each provider
names an environment variable in `[providers.<name>] api_key_env` (or uses a built-in
provider's own default, e.g. `OPENROUTER_API_KEY`), and Phil resolves that variable's value at
call time — see Keys below for where from.

### Tiers

Six roles — `orchestrator`, `architect`, `critic`, `implementer`, `tester`, `reviewer` —
resolve through two tiers by default: `high` for `architect`, `critic` and `reviewer`; `low`
for `orchestrator`, `implementer` and `tester`. An optional `classifier` tier falls back to
`low` when it has no model of its own. Remap a role to a different tier under `[tiers]`, or
give it its own model directly under `[models]`, which always wins over its tier:

```toml
[models]
high = "openrouter:openai/gpt-6-luna"
low = "openrouter:openai/gpt-6-sol"
critic = "anthropic:claude-opus-5"   # overrides critic's tier for just this role

[tiers]
tester = "high"                     # give the tester the strong model too
```

A `phil.toml` that sets every role individually (the old six-role style) keeps working
unchanged — each role's own `[models]` key always wins over any tier.

### Providers

A model is `provider:model`, e.g. `openrouter:openai/gpt-6-luna` or `ollama:qwen3:32b`. Five
providers are built in:

| Provider | Kind | Base URL | Key |
|---|---|---|---|
| `openrouter` | openrouter | OpenRouter's own | `OPENROUTER_API_KEY` |
| `openai` | openai | OpenAI's own | `OPENAI_API_KEY` |
| `anthropic` | anthropic | Anthropic's own | `ANTHROPIC_API_KEY` |
| `google` (alias `google_genai`) | google | Google's own | `GOOGLE_API_KEY` |
| `ollama` | openai | `http://localhost:11434/v1` | none |

`kind` picks the SDK: `openai` for any OpenAI-compatible server (including a custom endpoint
or a local one), plus `anthropic`, `google` and `openrouter`. Add a new provider, or override
a built-in's fields, under `[providers.<name>]` — for example a self-hosted vLLM server:

```toml
[providers.vllm]
kind = "openai"
base_url = "http://localhost:8000/v1"
api_key_env = "VLLM_API_KEY"   # leave out for a server that needs no key

[models]
low = "vllm:my-local-model"
```

Ollama needs no `[providers.ollama]` entry at all — it's already built in and keyless:

```toml
[models]
low = "ollama:qwen3:32b"
```

Migrating from an older config: a model prefix that `init_chat_model` used to accept but that
isn't built in here (e.g. `deepseek:`, `groq:`, `xai:`) now needs a `[providers.<name>]`
entry. Most such services are OpenAI-compatible:

```toml
[providers.groq]
kind = "openai"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
```

Prices, for estimating a call's cost when it reports none: any provider of kind `openrouter`
(the built-in one or a custom entry) is priced from OpenRouter's public price list. Every other
provider uses its own `input_per_mtok` / `output_per_mtok` (USD per million tokens) from its
`[providers.<name>]` entry; Ollama's built-in prices are `0.0`, so it costs $0. Anything else
shows as unknown (see Observability).

Every provider's own SDK retries are off (`max_retries=0`), so Phil's retry middleware is the
only retry policy that runs — except the Google kind, which is built with `max_retries=1`
(its SDK treats `0` as "use its own default retries", not "none").

### Keys

Each provider's API key comes from an environment variable — the environment first, then, as
a fallback, the OS keychain (through `keyring`). A stored key is only used when the variable
isn't exported; an exported variable always wins, even over a stored key for the same
provider.

    phil keys set <provider>      # prompts for the key (input hidden), stores it in the keychain
    phil keys list                # every provider this config uses, and where its key comes
                                   # from: env, keychain, or missing
    phil keys remove <provider>   # deletes the keychain entry only — any environment variable
                                   # of the same name is untouched

`phil setup` offers to save a key the same way, during its key step, and skips the offer
when the variable is already set in the environment. When no keychain is available here
(headless environments, containers, some CI), both `phil setup` and `phil keys set` say so and
tell you to export the variable instead of trying to store it. `phil keys` works anywhere, not
only inside a repository.

Commands agents run (the shell tool, gates and tests) get neither the key variables nor Phil's
stored keys: their environment drops secret-looking variables and pins `keyring` to its null
backend, so code they run can't read a stored key through `keyring`. That can't stop a
malicious test from reaching the OS keychain directly, so review what agents add.

### Checking your models

    phil models check

makes one tiny real call to each distinct model that some role resolves to, through the same
agent path Phil uses, and prints one line per model: pass or fail, the tiers it serves (or
`role:<name>` when a role's own key overrides its tier), the model string, and how long it
took (or why it failed: an unknown provider, a missing key, or a model that didn't return the
required structured output). A tier set under `[models]` that no role maps to — e.g. a global
`high` under a repo `phil.toml` that sets all six roles — isn't called; it's listed as
`– high  <model>  unused (no role maps to it)` and isn't a failure.

## Usage

Plan and start work from a chat in your repo (configure your models first — see
Configuration above):

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

## Routing

Each message is classified into a task class (question, diagnosis, a small operation, a
simple change, a focused fix, a feature, a refactor, design work, or a broad project), which
sets one of three paths:

- **answer** — a question or diagnosis: a read-only agent answers, with no run, worktree or
  commit. It reads a snapshot of the working tree (tracked and untracked, non-ignored files),
  so uncommitted edits are visible and ignored files such as `.env` are not. Without an
  `answerer` model of its own (`low`, or `[models] answerer`) it uses the orchestrator's.
- **quick** — a small, well-specified change: until M3b lands, this still goes through the
  full planning pipeline (the status line says "planning", not "quick path", so it doesn't
  claim a shortcut that isn't there yet).
- **full** — a feature, refactor, design question or broad project: the normal plan, run,
  review and PR flow.

The status line under your message says which path was taken and why, e.g. `Simple change ·
planning  (/quick and /full force a path)`, `Forced: full path` or `Forced: quick · planning`
(a forced quick goal is planned like any other until M3b). Accepting a diagnosis's `Fix it?`
offer says `Fix · planning`. Force a path yourself by
starting your message with `/ask`, `/quick` or `/full` — the prefix is stripped and the
classifier is skipped entirely. When the request is too ambiguous to route (a missing
target, conflicting goals, or no way to tell what done means), Phil asks first instead of
guessing.

Routing is decided by a classifier, not by the model doing the work. By default it's your
`low` model, called the same way the chat's other agents are. For faster, cheaper routing,
configure TypeSafe's Jev instead:

```toml
[models]
classifier = "typesafe:jev-latest"
```

    phil keys set typesafe   # prompts for TYPESAFE_API_KEY, stored in the keychain

If Jev errors (a timeout, an auth or rate-limit response, or a malformed reply), that one
message falls back to your `low` model automatically — Phil prints a dim `Router
unavailable ({reason}); using your low model.` note and carries on; routing never blocks the
chat. If the low model fails too, or there is none, the note reads `Router unavailable
({reason}); intake decides.` instead. `phil models check` pings a configured `typesafe` classifier the same way it checks
every other model.

Two thresholds under `[routing]` (defaults shown) control how readily routing defers to
intake instead of guessing:

```toml
[routing]
confidence_threshold = 0.5   # below it, intake decides the depth instead of the classifier
detail_threshold = 0.6       # at or above it, intake asks you something first
jev_timeout_s = 5.0          # Jev's connect/read timeout; no retries inside the adapter
```

A classifier benchmark (`tests/live/bench/classify/`) compares the Jev and low-model
backends on about 40 labelled requests — class and depth accuracy, the worst error (a
question routed to a change), `needs_detail` precision/recall, latency, cost, and a
threshold sweep:

    PHIL_BENCH_CONFIG=~/Code/phil-bench.toml uv run pytest -m bench tests/live/bench/classify -n 0
    uv run python -m tests.live.bench.classify.run --report

`--report` replays the stored answers (no new calls) and prints each backend's numbers, the
threshold sweep, and spec §5.1's decision rule for whether Jev is worth recommending over
the low model.

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

When a call reports no cost, Phil estimates one. A call to any provider of kind `openrouter`
(the built-in `openrouter` or a custom `[providers.<name>]` entry with that kind) is priced
from OpenRouter's public price list (`GET https://openrouter.ai/api/v1/models`, no key needed,
cached under `~/.phil/cache/` for a day). Any other provider uses its own `input_per_mtok` /
`output_per_mtok` prices set under `[providers.<name>]` (see Configuration → Providers).
Ollama's built-in prices are `0.0` / `0.0`, so it costs $0. A model with no price shows as
unknown, never estimated.

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
