# Contributing to Phil

## Development

    uv sync
    uv run pytest
    uv run phil --version

The distribution is named `phil-agent`. If you installed an editable `phil` tool before the
rename, remove it first, or the two tools clash over the `phil` command:

    uv tool uninstall phil

To use your working copy as `phil` from any repo, install it as an editable tool. Code changes
are picked up automatically; reinstall after dependencies change:

    uv tool install --editable . --force

The original LangGraph prototype is kept in `prototype/` for reference and is not part of the package.

Start a run from a plan file and follow it:

    uv run phil run plan.json
    uv run phil attach <run-id>

Phil keeps library warnings, other libraries' log lines and crash tracebacks out of the terminal.
They go to `~/.phil/logs/phil.log` (a chat's go to its own `phil.log`, a run's to its
`worker.log`), and a crash prints one line naming its file in `~/.phil/logs/`. To see them in the
terminal while developing, set `PHIL_DEBUG=1`:

    PHIL_DEBUG=1 uv run phil

## Tests

The tests fail on any warning raised from Phil's own code; fix its cause rather than ignoring it.

Tests run in parallel with pytest-xdist; use `uv run pytest -n 0` to run serially when debugging.
CI runs the same suite on Linux, macOS and Windows for every pull request.

Live tests call a real model through OpenRouter and are skipped by default. They read the key
you've already stored with `phil keys set openrouter` (or `phil setup`), the same way a normal
run does, so there's nothing to source first:

    uv run pytest -m live

An exported `OPENROUTER_API_KEY`, or `uv run --env-file … pytest -m live`, still wins over a
stored key, letting you point a run at a different one. Either way, a live test can only read
a stored key — it never changes or removes one.

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

To measure what memory saves, add `PHIL_BENCH_WARM=1`. Each case then runs as cold-then-warm
pairs on the same repo copy: a cold run with empty memory, then a warm run with whatever the cold
run learned. `PHIL_BENCH_PAIRS=N` sets the number of pairs per case (default 3); run-to-run
variance is large, so one pair says little:

    PHIL_BENCH_WARM=1 PHIL_BENCH_PAIRS=3 PHIL_BENCH_CONFIG=~/bench.toml uv run pytest -m bench -n 0
    uv run python -m tests.live.bench.report --pairs 3

The report adds a "Cold against warm" table with the median model calls, tokens and minutes of
the last `--pairs` cold and warm runs per case, and the change between them.

The routing classifier has its own benchmark; see Routing in the [user guide](docs/guide.md#routing).

## Releasing

Releases are tag-driven. The version is written in one place, `pyproject.toml`.

1. In a pull request, bump `version` in `pyproject.toml` and add a `## [X.Y.Z] - YYYY-MM-DD`
   section to `CHANGELOG.md`. A test fails if the section is missing.
2. Merge it, then tag the merge commit on `main` and push the tag:

       git tag vX.Y.Z
       git push origin vX.Y.Z

3. The `release` workflow then runs the checks below. If any fails, nothing is published:
   - the tag must equal `v` plus the version;
   - the changelog must have that version's section;
   - the full test suite;
   - the build, `twine check`, and an sdist under 2 MB;
   - a smoke test of the built wheel on Linux, macOS and Windows.

   It then publishes to PyPI with trusted publishing, installs the published version back from
   PyPI, and creates the GitHub Release with that changelog section as notes.

### Rehearsing on TestPyPI

Run the `release` workflow by hand (Actions → release → Run workflow). It runs the same checks
and publishes `X.Y.Z.dev<run number>` to TestPyPI, so every rehearsal has a new version. To try
that build locally, download its wheel and install it with dependencies from PyPI:

    uv run --no-project python .github/scripts/release_check.py fetch-wheel \
      https://test.pypi.org/pypi/phil-agent/X.Y.Z.devN/json .
    uv tool install ./phil_agent-X.Y.Z.devN-py3-none-any.whl --force
    phil --version
    phil setup

### One-time setup

Trusted publishing needs no API token, but each index has to know the workflow:

1. On PyPI (Account → Publishing → Add a new pending publisher): project `phil-agent`, owner
   `ahochoy`, repository `phil`, workflow `release.yml`, environment `pypi`.
2. On TestPyPI, the same with environment `testpypi`.
3. In this repository (Settings → Environments), create `pypi` and `testpypi`. Add yourself as
   a required reviewer on `pypi`, so every real publish waits for a click.
