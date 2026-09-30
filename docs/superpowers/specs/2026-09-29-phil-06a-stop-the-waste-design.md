# Phil 6a (Roadmap M1) — Stop the Waste

**Status:** approved in conversation 2026-09-29.
**Scope:** fix every cause of waste found in live run r-7d41, and measure the result with a live benchmark. **Deferred to M3 (proportional orchestration):** goal-size and task-class routing, lighter agents (no sub-agent or summarization), review findings patched directly, tighter retry limits, and the larger prompt and handoff redesign. See `docs/superpowers/roadmap.md`.

## 1. Problem

A one-tag change to an Astro site took about 50 minutes, 438 model calls and more than 3M input tokens (79k output), and then failed on its third task. The run's artifacts show the causes:

| Cause | Evidence |
|---|---|
| Exploration commands paused for approval | 13 approval escalations for `ls`, `grep`, `pwd`, `git status`, `cat`, each followed by a fresh agent that explored again: ls ×186, read_file ×166, run_shell ×193 |
| The evidence check rejected valid outputs | 26 outputs rejected with "claimed command was never run" for file-tool calls (grep, read_file, ls) the model listed as evidence |
| No memory between attempts | every retry, approval or resume starts a new agent from the task and the last error only (`invoke.py:250`, `engine.py:243-253`) |
| Wrong test command, frozen for the run | `test_cmd = "uv run pytest"` in an Astro repo; `plan.test_cmd` beats config and is fixed at run start |
| Test-first imposed where nothing is testable | a verification-only task (EGG-003) failed 3 red-phase attempts, since there was no failing test to write |
| Over-planning | one meta tag became 3 tasks, including a JSON data file "to match data-driven conventions" and a separate verification task |

## 2. Decisions (user, 2026-09-29)

| Topic | Decision |
|---|---|
| Untestable work | Per-task `check` mode now; goal-size tiers come later with M3. |
| Shell | Read-only commands are allowed by default. |
| Retry memory | Carry a work summary plus the current diff; don't resume the transcript. |
| Measurement | A live benchmark with fixture repos. |
| Prompts | Tighten them now (the shared block and architect task sizing); the full redesign waits for M3. |

## 3. Design

### 3.1 Benchmark (live, run by the user)

- **Fixtures** live in `tests/live/bench/fixtures/`:
  - `py-calc/`: a small Python package with a pytest suite.
  - `static-site/`: an HTML page with a layout partial and a `package.json` whose `build` script copies files into `dist/`. It has no test suite and no network or npm install; the `build` script is a plain `node` script.
- **Cases** (`tests/live/bench/cases.py`, each a goal plus a pass check that runs on the resulting branch):
  - `py-multiply`: "Add a multiply(a, b) function" (tdd). Passes when the tests pass and `multiply` exists.
  - `site-meta-tag`: "Add a hidden `<meta name=\"easter-egg\" content=\"hello world\">` to the layout head" (check). Passes when the built `dist/index.html` contains the tag.
  - `site-typo`: "Fix the typo 'Welcom' in the heading" (check). Passes when the source no longer contains 'Welcom'.
- **Runner:** `uv run pytest -m bench` (a new marker, excluded by default like `live`). For each case it:
  1. copies the fixture to a temp dir and runs `git init` and a commit;
  2. writes `phil.toml` from `PHIL_BENCH_CONFIG`, the path to a TOML file holding the user's `[models]` and any overrides; the case is skipped if that variable is unset;
  3. drives the chat pipeline without prompts: `Planner` (architect and critic) with the approval auto-accepted, then `run_worker(..., "start")` in the foreground with generous budgets;
  4. records a result.
- **Result:** one JSON line appended to `~/.phil/bench/results.jsonl`. Fields: case, timestamp, Phil git sha, models by role, plan task count and modes, pass/fail, run state, minutes, and from telemetry (planning and run) calls, model calls, input and output tokens, cost with its source, and retries.
- `python -m tests.live.bench.report` prints the last N results per case as a table: tokens, calls, minutes, cost, pass.
- The benchmark is run once on `main` before the changes as a baseline, and again after.

### 3.2 `check`-mode tasks

- `Task` gains `verify: Literal["tdd", "check"] = "tdd"` and `check_cmd: str | None = None`. The contract validator requires `check_cmd` when `verify == "check"`, and forbids it for `tdd` tasks.
- **Engine.** `pick_task` sets `phase = "green"` for `check` tasks (no red phase). `verify` for a check task:
  - runs the test command (no new failures against the baseline, as green does today);
  - runs `check_cmd` through the shell policy; its exit code 0 is the pass;
  - applies the existing change gates.

  The green phase's rule "must not modify test files" still applies. Failures use the same attempt loop.
- **Implementer input** carries `verify` and `check_cmd`. Its prompt says: for a check task, make the change and run `check_cmd`; write no tests.
- **Architect prompt:**
  - Use `check` only when there is no behaviour to test: copy, markup, static assets, config, docs.
  - Behaviour changes stay `tdd`.
  - Never plan a task whose only work is verification; put the check in the task that makes the change.
  - `check_cmd` is a single shell command, for example `npm run build`; the architect prefers commands the repo already defines.
- **Critic** flags a `check` task that changes behaviour, and any verification-only task.
- **Plan view** marks check tasks `(check: <cmd>)`. **Approval**: `check_cmd`s are validated against the shell policy like the test command, but read-only and plan-listed commands are allowed (§3.3).

### 3.3 Read-only shell by default

- `ShellPolicy` gains a built-in read-only set that is always allowed. It is separate from `[shell] allow` and not replaced by it:
  - `ls`, `pwd`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`;
  - `find` (refused when its arguments include `-exec`, `-execdir`, `-delete`, `-ok`, `-okdir`, `-fprint*`, or `-fls`);
  - `git status`, `git diff`, `git log`, `git show`, `git ls-files`, `git branch` (listing only: refused with `-d`, `-D`, `-m`, `-M`, `-c`, `-C`, or `--delete`).

  Arguments may be anything except the forbidden characters (unchanged: no pipes, chaining or redirects).
- **Run-scoped allowances.** The run's effective test command and every task's `check_cmd` are allowed for that run (exact match plus trailing arguments), whether or not they appear in `[shell] allow`. They were shown in the plan the user approved.
- **Paths.** A read-only command naming a path outside the worktree (absolute or `..`) is refused, not escalated: "stay inside the worktree". The phase retry carries it as feedback.
- Launch validation changes accordingly. An unapproved test command still blocks a run only if it is neither in config nor in the plan the user is approving.

### 3.4 Evidence

- `Claim.command` is described as "the exact shell command you ran, or the tool call you made (e.g. `read_file src/x.ts`, `grep 'egg' src/`)".
- `check_evidence` checks only claims whose command's first word is a shell program (not one of the file-tool names `ls`, `read_file`, `glob`, `grep`, `write_file`, `edit_file`, `delete`, `task`). File-tool claims are accepted when the tool log shows that tool was called. `UsageCollector` already counts tool starts by name, so the check receives those names. A claimed tool that was never called is still a problem.
- Shell claims are matched as today.

### 3.5 Work summary between attempts

- `TaskResult` gains a `worklog` object: `files_read` (paths), `files_changed` (paths), `notes` (at most 5 short strings: what was tried, what failed, what's next). The implementer fills it on every output.
- The engine stores the latest worklog per task in run state (`worklogs[task_id]`). When an attempt fails to produce output (a rejection or crash), the engine builds a fallback worklog from the tool log: the `read_file` and `ls` paths from `UsageCollector` tool inputs, and the changed files from the worktree.
- `ImplementInput` gains `worklog` (the previous attempt's) and `diff` (the task's current changes since its start SHA, capped by the packet budget). The implementer prompt says: continue from the worklog and diff; don't re-read files listed there unless you need their current contents.
- An approval resume, a phase retry and a new phase of the same task all carry it.

### 3.6 Test command

- **Detection.** When a plan has no `test_cmd` and `[project] test_cmd` is unset, Phil proposes one from the repo:
  - `package.json` → `npm test` if a `test` script exists, else none;
  - `pyproject.toml`/`pytest.ini`/`conftest.py` → `uv run pytest` if `uv.lock` exists, else `pytest`;
  - `go.mod` → `go test ./...`;
  - `Cargo.toml` → `cargo test`.

  The architect sees the detected command as a hint. The plan view shows the effective command and where it came from (plan, config, or detected).
- **No test suite.** If nothing is detected and the plan's tasks are all `check`, the run may proceed with no test command: gates then run only the `check_cmd`s. A plan with any `tdd` task still needs a test command.
- **Resume.** When the worker resumes a run, it compares the stored `test_cmd` with the current effective one: `phil.toml`'s `[project] test_cmd` if set, else the plan's. If they differ, the run switches to the new command. A `test_cmd_changed` event is written, and the chat and `phil attach` print `Using the updated test command: <cmd>.` The baseline is re-captured before the next gate.

### 3.7 Prompt tightening

- **Shared block** (`prompts/_shared.md`), added for all agents:
  - explore with the file tools rather than the shell;
  - don't re-read what's in your input, worklog or diff;
  - match the repository's conventions; add no files, abstractions or features the task doesn't require;
  - stop as soon as the acceptance criteria are met;
  - keep outputs short and structured, since other agents read them.
- **Architect:** use the fewest tasks that keep each one independently verifiable; one small change is one task; don't split a change to mirror patterns (for example, a data file for a single string); every task is either `tdd` or `check` (§3.2).
- Every prompt change is checked with the benchmark: a change that makes a case worse doesn't ship. Prompt-contract tests (e.g. `test_contract_descriptions.py`) are updated.

## 4. Testing

- **Offline, with scripted agents.** Check-task engine flow (no red phase; `check_cmd` pass and fail; test files untouched); `Task` validation; the read-only shell set and its refusals (find `-exec`, `git branch -D`, paths outside the worktree); run-scoped allowances; evidence with file-tool claims; worklog storage, the fallback from the tool log, and its presence in the next packet; test-command detection per ecosystem; resume switching the test command (with an event); planning prompt and contract changes.
- **Live, run by the user:** `uv run pytest -m bench`, run on `main` (baseline) and on the branch. Expected: each case passes; `site-meta-tag` and `site-typo` plan one `check` task with no red phase; `py-multiply` plans one `tdd` task; tokens and minutes drop sharply against the baseline.
