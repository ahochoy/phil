# Phil 6a — Stop the Waste Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the causes of waste found in live run r-7d41 (approval churn, spurious evidence rejections, no memory between attempts, a stale or wrong test command, test-first on untestable work, over-planning), and measure the result with a live benchmark.

**Architecture:**
- A `bench` pytest marker drives the real planner and worker against two fixture repos and appends results to `~/.phil/bench/results.jsonl`.
- `ShellPolicy` gains a built-in read-only set, run-scoped allowances and worktree containment.
- `Task` gains `verify: tdd|check` with a `check_cmd`, and the engine skips the red phase for `check` tasks.
- `TaskResult` gains a `worklog` that the engine carries, with the task's diff, into the next attempt.
- The test command is detected from the repo and refreshed on resume.
- Prompts gain a shared efficiency block and tighter task-sizing rules.

**Tech Stack:** Python 3.14, uv, pytest (xdist), pydantic contracts, LangGraph engine, deepagents/langchain agents, typer, rich.

**Spec:** `docs/superpowers/specs/2026-09-29-phil-06a-stop-the-waste-design.md` (roadmap: `docs/superpowers/roadmap.md`, M1)

## Global Constraints

- Tests never touch the network, a real LLM or the real `gh`. The benchmark is opt-in (`-m bench`), is excluded by default like `live`, and is run by the user.
- The read-only set is always allowed and is not replaced by `[shell] allow`:
  - `ls`, `pwd`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`;
  - `find`, unless an argument is `-exec`, `-execdir`, `-delete`, `-ok`, `-okdir`, `-fls`, or starts with `-fprint`;
  - `git status`, `git diff`, `git log`, `git show`, `git ls-files`, and `git branch`, where `git branch` is refused with `-d`, `-D`, `-m`, `-M`, `-c`, `-C` or `--delete`.
- The forbidden characters (pipes, chaining, redirects, `$`, backticks, newlines) and the existing risky-flag denials are unchanged and always win.
- A read-only command naming a path outside the worktree (an absolute path not under it, or `..` escaping it) is refused (`forbidden`), never escalated.
- `Task.verify` is `"tdd"` (the default) or `"check"`. `check_cmd` is required for `check` and forbidden for `tdd`. `check` tasks have no red phase.
- The worklog allows at most 5 notes, each 200 characters or fewer, and at most 50 paths per list.
- Test-command detection, in order: `package.json` with a `test` script gives `npm test`. Python markers (`pyproject.toml`, `pytest.ini`, `conftest.py`) give `uv run pytest` when `uv.lock` exists, else `pytest`. `go.mod` gives `go test ./...`. `Cargo.toml` gives `cargo test`. Otherwise there is none.
- Resume message: `Using the updated test command: <cmd>.`
- `phil.cli.main`, `phil.chat.*`, `phil.packets`, `phil.agents.invoke`, `phil.publish.*` and `phil.workspace.*` must not import langgraph/langchain/deepagents at module level.
- Escape user/agent text printed through rich with `escape`.
- Commit messages end with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

- `tests/live/bench/` (create): `__init__.py`, `fixtures/py-calc/`, `fixtures/static-site/`, `cases.py`, `harness.py`, `test_bench.py`, `report.py`
- `pyproject.toml`: the `bench` marker, and `addopts` excludes it
- `src/phil/workspace/shell.py`: read-only set, `extra_allow`, worktree containment
- `src/phil/agents/tools.py`, `src/phil/agents/invoke.py`, `src/phil/agents/spec.py` or the `AgentContext` module: pass run-scoped allowances and the worktree to the policy; pass tool names to the evidence check
- `src/phil/agents/evidence.py`: accept claims that cite file tools
- `src/phil/contracts/planning.py` (`Task`), `contracts/results.py` (`TaskResult`, `Worklog`), `contracts/inputs.py` (`ImplementInput`), `contracts/common.py` (`Claim` description)
- `src/phil/run/engine.py`: check tasks, worklogs, the diff in the packet, the run-scoped shell allowances
- `src/phil/run/worker.py` and `src/phil/run/runner.py`: test command switch on resume
- `src/phil/repo_detect.py` (create): `detect_test_cmd(root) -> str | None`
- `src/phil/chat/approval.py`, `src/phil/chat/planning.py`, `src/phil/ui/plan_view.py`: detection hint, check display, validation
- `src/phil/prompts/_shared.md`, `architect.md`, `critic.md`, `implementer.md`
- Docs: `README.md`, `docs/superpowers/roadmap.md` (M1 status), `docs/superpowers/plans/2026-09-29-phil-06a-followups.md`

---

### Task 1: Live benchmark harness

**Files:** Create `tests/live/bench/` (listed above). Modify `pyproject.toml`.

**Interfaces:**
- Produces:
  - `Case(name: str, fixture: str, goal: str, expect_modes: tuple[str, ...], passed: Callable[[Path], bool])` in `cases.py`, and `CASES: list[Case]`.
  - `run_case(case: Case, config_path: Path, work: Path) -> dict` in `harness.py`. It returns the result record and appends it to `~/.phil/bench/results.jsonl`, or to `$PHIL_BENCH_RESULTS` if set.
  - `report.main(argv)` prints a table.

**Fixtures:**
- `py-calc/`:
  - `pyproject.toml`: name `calc`, with a `[tool.pytest.ini_options]` section;
  - `calc/__init__.py`: `add(a, b)` and `subtract(a, b)`;
  - `tests/test_calc.py`: two passing tests.
- `static-site/`:
  - `src/layout.html`: an `<html><head><title>Site</title></head><body>{{content}}</body></html>` template;
  - `src/index.html`: content `<h1>Welcom to the site</h1>`;
  - `build.mjs`: plain Node, no dependencies; it writes `dist/index.html` by substituting the index into the layout;
  - `package.json`: `{"name":"site","private":true,"scripts":{"build":"node build.mjs"}}`, no `test` script.

**Cases:**

| Case | Goal | Expected modes | Passes when |
|---|---|---|---|
| `py-multiply` | "Add a multiply(a, b) function to calc." | `("tdd",)` | `calc/__init__.py` defines `multiply`, and `pytest -q` in the worktree exits 0 |
| `site-meta-tag` | `Add a hidden <meta name="easter-egg" content="hello world"> to the page head.` | `("check",)` | `node build.mjs` exits 0, and `dist/index.html` contains `name="easter-egg"` |
| `site-typo` | "Fix the typo 'Welcom' in the page heading." | `("check",)` | no file under `src/` contains `Welcom` |

`expect_modes` is recorded, not asserted: before Task 4 lands there are no modes. The result stores the plan's actual `verify` values, or `["tdd", …]` when the field doesn't exist yet.

**Harness:**
1. Copy the fixture into `work/<case>`. Run `git init -b main`, set the user name and email, `git add -A`, then commit.
2. Write `phil.toml` from the file at `PHIL_BENCH_CONFIG`. Deep-merge it over a harness baseline:

   ```toml
   [run]
   max_tokens = 5000000
   max_cost_usd = 10.0
   ```

   The case skips with a clear reason when `PHIL_BENCH_CONFIG` is unset.
3. Plan with the real `Planner`:
   - `Planner(ctx, repo_overview(root)).draft(Goal(text=goal), tree=root)`;
   - `ctx` is an `AgentContext` with `layer="chat"`, `chat_id=f"bench-{case}-{stamp}"` and a connection from `connect(ProjectPaths(slug).db_path)`;
   - check `planning.py` for the exact `Goal` constructor and `draft` signature.
4. Start the run and drive it in the foreground:
   - `prepare_run(info, draft.plan, info.head_sha, chat_id=ctx.chat_id)`;
   - `run_worker(root, run_id, "start")`, with no factory override so real models are used.
5. On exit, read the run's state. If the state is `escalated`, the case fails with the escalation summary recorded, since the benchmark answers nothing.
6. Evaluate `case.passed` on the run's worktree before any cleanup.
7. Build the record:
   - `case`, `ts`, `phil_sha` (`git rev-parse --short HEAD` of the Phil repo), `models` (from the config);
   - `tasks`, `modes`, `state`, `passed`, `minutes`;
   - `tokens_in`, `tokens_out`, `cost_usd`, `cost_source`: from `chat_usage(conn, chat_id)` split by direction, using the telemetry columns;
   - `model_calls` and `retries`: `SUM` over telemetry rows for the chat and its run.
8. Append the record as one JSON line.

**Test:**
- `test_bench.py`: one `@pytest.mark.bench` test parametrized over `CASES`. It calls `run_case`, prints the record, and asserts `record["passed"]`.
- Add `bench: runs the live benchmark (opt-in with -m bench)` to the markers.
- Change `addopts` to `-m 'not live and not bench' -n auto`.
- The autouse network and `gh` guards in `tests/conftest.py` must exempt `bench` like `live`: extend `_is_live`, or add `_is_bench`, so real price fetching works.

**Report:** `python -m tests.live.bench.report [--last N]` prints one row per record for the last N per case (default 5): case, sha, tasks/modes, state, passed, minutes, calls, model calls, tokens in/out, cost.

**Offline tests (default suite):**
- `tests/live/bench/test_harness_offline.py` (unmarked) exercises `run_case` with a `ScriptedAgentFactory`:
  - add an injectable `factory` parameter to `run_case`, defaulting to real models;
  - use a trivial scripted plan on `py-calc`;
  - assert that one well-formed record with all fields is appended to a temp results file.
- It also checks that `report.main` prints the record.

- [ ] Write the offline tests (red), build the fixtures, `cases.py`, `harness.py`, `report.py` and the marker (green), then run the full suite.
- [ ] Commit: `Add a live benchmark for small goals`

---

### Task 2: Read-only shell by default, run-scoped allowances, worktree containment

**Files:**
- Modify: `src/phil/workspace/shell.py`, `src/phil/agents/tools.py`, `src/phil/agents/invoke.py` and the `AgentContext` definition (find it with `grep -n "class AgentContext" -r src/phil`), `src/phil/run/engine.py` (`_context`), `src/phil/chat/approval.py`.
- Test: `tests/workspace/test_shell_policy.py` (extend the existing shell tests), `tests/chat/test_approval.py`.

**Interfaces:**
- Produces:
  - `READ_ONLY: tuple[str, ...]`.
  - `ShellPolicy(allow: list[str], *, extra_allow: Iterable[str] = (), root: Path | None = None)`.
  - `denial_reason(command) -> str | None`: `None`, `"forbidden"` or `"not_allowed"`, unchanged.
  - `refusal_detail(command) -> str | None`: a one-line reason for `forbidden`; the containment case returns `"stay inside the worktree"`.
  - `AgentContext.extra_allow: tuple[str, ...] = ()`.
  - `make_shell_tool(..., extra_allow=())`, which builds `ShellPolicy(shell.allow, extra_allow=..., root=workdir)`.

**Rules:**
- The order of checks is:
  1. forbidden characters and risky flags;
  2. read-only commands, with their argument refusals and the containment check;
  3. `extra_allow` patterns;
  4. `[shell] allow` patterns;
  5. otherwise `not_allowed`.
- Read-only matching is by argv. `git` subcommands match on `argv[1]`.
- **Containment:** when `root` is set, any argument of a read-only command that looks like a path is resolved against `root`. An argument counts as a path if it is not a flag and either contains `/` or is `..`. If it resolves outside `root`, the command is `forbidden`.
- `extra_allow` entries match the exact command, or the command with trailing arguments (like a pattern with a trailing `*`).
- In the engine, `_context` passes `extra_allow = (state["test_cmd"], *check_cmds)` for implementer and tester calls. Before Task 4 there are no `check_cmds`, so only the test command is passed. Read the plan with `getattr(task, "check_cmd", None)` so the task is independent of Task 4.
- `run_shell`'s REFUSED message includes `refusal_detail`.
- **Approval** (`test_cmd_problem`): the effective test command is also acceptable when it is the plan's own command, since the user approves the plan. The only rejection left is `forbidden`. Keep the missing-command message; Task 6 changes it.

**Tests:**
- Each read-only command is allowed with an empty `allow`.
- `find . -name x` is allowed; `find . -delete` and `find . -exec rm {} ;` are forbidden (the `;` is already forbidden, so also check `-exec` without it).
- `git branch` is allowed; `git branch -D x` is forbidden.
- `git log --oneline` is allowed.
- `cat /etc/passwd` and `ls ../..` are forbidden when a root is set.
- `cat src/x.ts` is allowed.
- `ls -la` is allowed (flags aren't paths).
- `npm run build` is `not_allowed` by default, and allowed with `extra_allow=("npm run build",)`, including with trailing arguments.
- `grep x | head` is still forbidden.
- Setting `[shell] allow = ["npm test"]` doesn't remove `ls`.
- Approval: a plan `test_cmd` of `npm run build`, not in the allow list, is now acceptable; a forbidden one still isn't.
- Engine (scripted): an implementer that runs `ls src` produces no escalation. Add a small scripted-agent test that calls the shell tool, using the pattern from the existing engine approval tests in `tests/run/test_engine_approval.py`.

- [ ] Red, green, full suite.
- [ ] Commit: `Allow read-only shell commands and the plan's own commands by default`

---

### Task 3: Evidence may cite file-tool calls

**Files:** Modify `src/phil/agents/evidence.py`, `src/phil/agents/invoke.py` (the call at about line 309), and `src/phil/contracts/common.py` (`Claim.command` description). Test in `tests/agents/test_evidence.py`, or wherever the existing evidence tests are.

**Interfaces:** `check_evidence(output, *, commands: list[str], workdir: Path | None, tools: Iterable[str] = ()) -> list[str]`. `invoke_agent` passes `tools=collector.tool_calls.keys()`.

**Rule:** a claim with a command is accepted if its tokens match a shell command that ran, as today, or if its first token names a tool that was called. The `run_shell` tool's own name counts as a shell call, so claims naming it still go through command matching. Otherwise the claim gets `claimed command was never run: …`, as today.

`Claim.command` description: `"The exact shell command you ran, or the tool call you made (e.g. read_file src/app.ts, grep 'egg' src/). Never list one you did not run."`

**Tests:**
- `read_file src/x.ts` is accepted when `read_file` was called, and rejected when it wasn't.
- `grep egg src/` is accepted when either the grep tool was called or `grep egg src/` ran as a shell command.
- An unknown shell command that never ran is still rejected.
- Update any contract-description test snapshot, e.g. `tests/test_contract_descriptions.py`.

- [ ] Red, green, full suite.
- [ ] Commit: `Accept evidence that cites file-tool calls`

---

### Task 4: `check`-mode tasks

**Files:**
- Modify: `src/phil/contracts/planning.py` (`Task`), `src/phil/run/engine.py` (`pick_task`, `implement`, `verify`), `src/phil/run/gates.py` (a check gate), `src/phil/ui/plan_view.py`, `src/phil/chat/approval.py` (validate `check_cmd`s), `src/phil/prompts/implementer.md`, `architect.md`, `critic.md`.
- Test: `tests/test_contracts.py`, `tests/run/test_engine_check.py` (create, following `tests/run/test_engine_happy.py`), `tests/test_ui.py` (plan view), `tests/chat/test_approval.py`.

**Interfaces:**
- `Task.verify: Literal["tdd", "check"] = Field("tdd", description=…)`.
- `Task.check_cmd: str | None = Field(None, description=…)`.
- A `model_validator` enforces: check requires `check_cmd`; tdd forbids it.
- `TaskResult.phase` stays `red|green`; check tasks report `green`.
- `verify_check(report: TestReport, check: ShellResult | None, red_snapshot: dict, now_snapshot: dict, base_passed, base_skipped) -> list[str]` in `gates.py`. It gives the green problems plus `check command failed (exit N): <cmd>` with the last lines of output.

**Engine:**
- `pick_task`: set `phase = "green" if task.verify == "check" else "red"`, and for check tasks `red_snapshot = self._test_snapshot()`, the current test-file contents. Use whatever `verify` uses to build `red_snapshot` today, so "must not modify test files" works unchanged.
- `implement`: in green with an empty `red_tree`, reset to `task_base_sha` rather than `restore_snapshot("")`.
- `verify`, for check tasks:
  1. run the tests as for green;
  2. run `task.check_cmd` in the worktree through `run_command`, with the configured shell timeout and the test log naming convention (`check-<task>-<seq>`);
  3. apply `verify_check`;
  4. on success, continue as green success does (commit, tester_task, and so on).
- `ImplementInput` needs no new field (the task carries `verify` and `check_cmd`).
- The implementer prompt adds: "If the task's `verify` is `check`: make the change, run its `check_cmd` and confirm it succeeds; write no tests."

**Prompts:**
- Architect: when to use `check`, the ban on verification-only tasks, and `check_cmd` being a single command the repo already defines (the spec's §3.2 wording).
- Critic checklist: "A `check` task whose change has behaviour that should be tested" and "A task whose only work is verification".

**Plan view:** show `  (check: <cmd>)` after a check task's description, escaped.

**Approval:** each `check_cmd` must not be `forbidden` (reason: `check command {cmd!r} uses shell operators or a blocked command`). Anything else is allowed at run time through `extra_allow`: the engine passes the `check_cmd`s via Task 2's `_context`.

**Tests:**
- Contract validation, both ways.
- Engine with scripted agents:
  - a check task whose implementer edits `index.html`, with a `check_cmd` of `grep -q easter dist/index.html` or a script in the fixture, passes with one implement call and no red phase;
  - a failing `check_cmd` retries with the problem in the feedback;
  - a check task that edits a test file fails the gate.
- Plan view text.
- Approval of a forbidden `check_cmd`.

- [ ] Red, green, full suite.
- [ ] Commit: `Add check-mode tasks that skip the red phase`

---

### Task 5: Work summary carried between attempts

**Files:** Modify `src/phil/contracts/results.py`, `src/phil/contracts/inputs.py`, `src/phil/run/engine.py`, `src/phil/run/state.py` (`RunState.worklogs`), `src/phil/agents/collector.py` (record tool input paths), and `src/phil/prompts/implementer.md`. Test in `tests/run/test_engine_worklog.py` (create) and `tests/test_contracts.py`.

**Interfaces:**
- A `Worklog` Part:
  - `files_read: list[str] = Field(default_factory=list, max_length=50)`;
  - `files_changed: list[str] = Field(default_factory=list, max_length=50)`;
  - `notes: list[Annotated[str, StringConstraints(max_length=200)]] = Field(default_factory=list, max_length=5)`.
- `TaskResult.worklog: Worklog = Field(default_factory=Worklog, description=…)`.
- `ImplementInput.worklog: Worklog | None = None` and `ImplementInput.diff: str = ""`.
- `RunState.worklogs: dict[str, dict]`. `UsageCollector.tool_paths: dict[str, list[str]]` records the `path` or `file_path` argument of `read_file`, `ls`, `glob` and `grep` calls, parsed from the tool input: `input_str`, or the `inputs` kwarg when present. It keeps at most 50 per tool.
- How the collector's paths reach the engine: `invoke_agent` returns them. Add them to the returned result object, or record them in the `AgentContext` / command log object. Choose the least invasive route, keep it typed, and note the choice in the report.

**Engine:**
- After an implement call, store `worklogs[task.id]`. Use the output's worklog when the output was accepted. Otherwise build a fallback: `files_read` from the tool paths, `files_changed` from `changed_files(worktree, since=task_base_sha)`, and `notes = problems[:2]`.
- The next implement call for the same task gets:
  - `worklog = worklogs.get(task.id)`;
  - `diff = self.worktrees.diff(worktree, task_base_sha)`, trimmed to the packet budget. `build_packet` already fits the contract to a budget; if the diff alone overflows, truncate it to its first 8,000 characters and add `…(diff truncated)`.
- This applies after a failed attempt, an approval resume, a deny resume, and from red to green.

**Implementer prompt:** "If your input has a worklog and diff, continue from them: don't re-read files listed in `files_read` unless you need their current contents. Fill `worklog` in your output: files you read, files you changed, up to 5 short notes (what you tried, what failed, what's next)."

**Tests:**
- Contract limits.
- A scripted two-attempt task: the first attempt is rejected, then the second packet contains the fallback worklog's `files_changed` and a non-empty diff.
- An accepted red output's worklog appears in the green packet.
- Collector path extraction from a synthetic `on_tool_start`.

- [ ] Red, green, full suite.
- [ ] Commit: `Carry a work summary and the task's diff between attempts`

---

### Task 6: Test-command detection and refresh on resume

**Files:**
- Create: `src/phil/repo_detect.py`.
- Modify: `src/phil/chat/approval.py`, `src/phil/chat/planning.py` (the hint to the architect), `src/phil/ui/plan_view.py` (the source label), `src/phil/run/worker.py`, `src/phil/run/runner.py` or `engine.py` (the switch), `src/phil/chat/watcher.py` and `src/phil/cli/attach.py` (render the new event).
- Test: `tests/test_repo_detect.py`, `tests/chat/test_approval.py`, `tests/run/test_worker.py`.

**Interfaces:**
- `detect_test_cmd(root: Path) -> str | None`, per the Global Constraints order.
- `effective_test_cmd(plan, config, root: Path | None = None) -> tuple[str | None, str]` returns `(cmd, source)`, where `source` is `"plan"`, `"config"`, `"detected"` or `"none"`. Update every caller.
- Architect input gains `detected_test_cmd: str | None`: add it to the architect input contract in `contracts/inputs.py`, as the `ArchitectInput` or equivalent.

**Rules:**
- No test command at all is acceptable only when every task is `check`. Otherwise the existing message stands.
- The worker's `start` uses `plan.test_cmd or config.project.test_cmd or detect_test_cmd(root) or ""`.
- **Resume switch:** in `worker.py`, for modes that continue a run (`resume`/`continue`), compute `desired = config.project.test_cmd or plan.test_cmd`. If it is set and differs from the checkpointed `state["test_cmd"]`:
  1. update the checkpoint with `graph.update_state(thread_config(run_id), {"test_cmd": desired, "rebaseline": True})`;
  2. append a `test_cmd_changed` event with `cmd`;
  3. log it.
- The engine's next `_test` call, when `state.get("rebaseline")` is set, first re-captures the baseline. It runs `desired` on the run's base in a temporary detached worktree (`git worktree add --detach <tmp> <base_sha>`, run, then `git worktree remove --force`), then sets `baseline_failures`, `initial_baseline`, `base_passed`, `base_skipped` and `rebaseline=False`.
- The watcher posts `test_cmd_changed`; the chat and `phil attach` print `Using the updated test command: <cmd>.`, escaped.

**Tests:**
- Detection per ecosystem, including priority when both `package.json` (without a `test` script) and Python markers exist: the Python command wins.
- `effective_test_cmd` sources.
- All-check plans with no command pass approval; a tdd task with no command is rejected.
- Worker resume with a changed config: the event is written, the state's `test_cmd` is updated, and a rebaseline happens once. Use the scripted-worker scenarios in `tests/run/worker_scenarios.py`.
- Chat/attach rendering.

- [ ] Red, green, full suite.
- [ ] Commit: `Detect the test command and pick up changes on resume`

---

### Task 7: Prompt tightening

**Files:** Modify `src/phil/prompts/_shared.md` and `architect.md`. Test with `tests/test_contract_descriptions.py` or the prompt-loading tests (`grep -rn "_shared" tests`).

**Shared block**, appended verbatim to `_shared.md`:

```markdown
## Working efficiently
- Explore with the file tools (ls, read_file, glob, grep), not the shell.
- Don't re-read what is already in your input, worklog, or diff.
- Match the repository's existing conventions. Add no files, abstractions, or features the task doesn't require.
- Stop as soon as the acceptance criteria are met.
- Keep outputs short and structured: other agents read them, not people.
```

**Architect**, added under its task-sizing guidance:

```markdown
- Use the fewest tasks that keep each one independently verifiable. One small change is one task.
- Don't split a change to mirror patterns (for example, a data file for a single string).
- Every task is either `tdd` (behaviour a test can pin down) or `check` (copy, markup, assets, config, docs, verified by `check_cmd`).
```

**Tests:** the shared block reaches every agent's system prompt (implementer, tester, reviewer, architect, critic, intake, btw), and the architect prompt contains the sizing rules.

- [ ] Red, green, full suite.
- [ ] Commit: `Tighten agent prompts for efficiency`

---

### Task 8: Docs and follow-ups

**Files:** Modify `README.md` and `docs/superpowers/roadmap.md`. Create `docs/superpowers/plans/2026-09-29-phil-06a-followups.md`.

**README:**
- "Shell commands": the read-only defaults, their refusals, and that the plan's test and check commands run without approval.
- "Plans": `check` tasks.
- "Test command": the detection order and the resume refresh.
- "Benchmark": `PHIL_BENCH_CONFIG=~/bench.toml uv run pytest -m bench`, then `python -m tests.live.bench.report`, where to find results, and how to run a baseline.

**Roadmap:** mark M1 in progress or done, with a link to this plan.

**Followups:** deferred review minors, plus the M3 items already listed in the spec's scope line.

- [ ] Write the docs, run the full suite.
- [ ] Commit: `Document the efficiency changes and the benchmark`

---

**Live check (user):**
1. At Task 1's commit: `PHIL_BENCH_CONFIG=<models toml> uv run pytest -m bench` (the baseline).
2. After Task 8: the same command, then `python -m tests.live.bench.report`.
3. Compare tokens, calls, minutes and pass rate. Expect `site-*` cases to plan one `check` task and `py-multiply` one `tdd` task.
