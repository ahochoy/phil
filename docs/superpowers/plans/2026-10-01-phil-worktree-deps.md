# Phil: Dependencies in Run Worktrees Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run worktrees get their dependencies through a setup command, and a command that can't run (exit 127) stops the run instead of passing quietly.

**Architecture:** A setup command (configured, or detected from the lockfile) runs in the engine's `setup` node before the baseline. `TestReport.exit_code` lets the gates tell "couldn't run" apart from "failed". `launch_problems` checks programs on PATH before launch, and the chat shows the setup command at approval and on the quick-start line.

**Tech stack:** Python 3.14, pydantic, LangGraph, pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-phil-worktree-deps-design.md`

## Global Constraints

- **Config:**
  - `[project] setup_cmd: str | None = None`: `None` detects, and `""` means no setup.
  - `[project] setup_timeout_s: int = 600`, validated `> 0`.
- **Detection order:** only when `package.json` exists.
  1. `pnpm-lock.yaml` → `pnpm install --frozen-lockfile`
  2. `yarn.lock` → `yarn install --frozen-lockfile`
  3. `bun.lockb` / `bun.lock` → `bun install --frozen-lockfile`
  4. `package-lock.json` → `npm ci`

  With no lockfile, return `None`.
- **Setup environment:** setup runs with `child_env(os.environ, config.shell.pass_env)`, so no keys reach it.
- **Exact strings:**
  - `` Setup command `{cmd}` failed (exit {n}); see the setup log. ``
  - `` Setup command `{cmd}` timed out after {s}s. ``
  - ``` `{cmd}` couldn't run: {first_line}. Dependencies may be missing in the run's worktree — set [project] setup_cmd (e.g. npm ci) — or the program isn't on PATH. ```
  - ``` `{prog}` (from the {kind} command `{cmd}`) isn't on PATH for Phil's runs ```, where kind is `setup`, `test` or `check`.
  - the plan-view line `setup: {cmd}`, with ` (detected)` when detected;
  - the quick-start line `Quick change: {id} {desc} · setup: {setup_cmd} · tests: {test_cmd}`, with each part present only when set.
- **Escalations:** `setup_failed` and `cmd_not_found` both offer `["retry", "abort"]`. Their `resume_to` is `"setup"`, or `"implement"` for a check that can't run.
- **Tests** stay offline. Commits use the exact trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, with the message written by the Write tool and committed with `git commit -F`. Edit files only with the Edit or Write tools. Never work around a hook.

## Review Focus

1. **A setup command that hangs.** It must stop at `setup_timeout_s` and escalate, not block the worker forever. Tested in Task 2.
2. **A retry after `setup_failed`.** It must rerun setup, then the baseline. The baseline must not run first, or it will see a half-installed tree. Tested in Task 2.
3. **`setup_cmd = ""` in a Node repo.** It means no setup, and no detection. Tested in Task 1.
4. **A full path to a program, such as `./node_modules/.bin/vitest`.** The PATH preflight must not flag it as missing. Tested in Task 1.
5. **A run started before this change, then resumed.** There's no setup-related state, so it must not crash. Tested in Task 2.

---

### Task 1: Config, detection, effective command and preflight

**Files:**
- `src/phil/config.py` (`ProjectConfig`)
- `src/phil/repo_detect.py` (`detect_setup_cmd`)
- `src/phil/chat/approval.py` (`effective_setup_cmd`, `program_problems`, and `launch_problems` calling it)
- Tests: `tests/test_repo_detect.py`, `tests/test_config.py`, `tests/chat/test_approval.py` (find the existing approval and launch_problems tests)

**Produces:**
- `detect_setup_cmd(root: Path) -> str | None`
- `effective_setup_cmd(config: PhilConfig, root: Path | None) -> tuple[str | None, str]`, where the source is `"config"`, `"detected"` or `"none"`
- `program_problems(plan, config, root) -> list[str]`
- `launch_problems(...)`, which now includes `program_problems` for the setup command, the effective test command and the check commands

Steps (test-driven):
1. Write failing tests for:
   - each lockfile;
   - no lockfile;
   - no `package.json` (Python repo);
   - `setup_cmd` configured, empty, and None with detection;
   - `setup_timeout_s` must be `> 0`;
   - the program checks: a missing bare program is reported with the exact string; an existing one isn't (use `sys.executable`'s basename, or monkeypatch `shutil.which`); a `./x/y` path is skipped; a command that fails `shlex.split` is skipped (other checks cover it).
2. Implement the code to make them pass.
3. Run the full suite once, then commit `Detect a run's setup command and check its programs before launch`.

### Task 2: The engine runs setup and stops on commands that can't run

**Files:**
- `src/phil/contracts/results.py` (`TestReport.exit_code`)
- `src/phil/run/gates.py` (`run_tests` and `run_check` fill `exit_code`; check `run_check`'s return type and carry the exit code through what it returns)
- `src/phil/run/engine.py`
- Tests: `tests/run/test_engine_setup.py` (new), plus the existing gates tests

**Consumes:** `effective_setup_cmd` (Task 1).

The engine changes:
- **`setup`:** before the baseline, compute `effective_setup_cmd(self.deps.config, self.deps.worktree)` and run it. A failure or timeout escalates `setup_failed` (Global Constraints). Write the log with `self.deps.artifacts.write_log("setup", output)`.
- **Baseline exit 127:** escalate `cmd_not_found`, with the first non-empty line of the log text as `first_line`.
- **`verify`:** a check-mode result with exit 127 escalates `cmd_not_found` with `resume_to="implement"` and doesn't increment `attempts`.
- **The graph:** add `"setup"` to `route_after_escalate`'s targets in `build()`. Check the `retry` action's behaviour when `resume_to` is `setup`.
- **Old runs:** a resumed run started before this change has no setup-related state. Use `.get` everywhere.

Steps (test-driven):
1. Write failing tests, using the existing run test harness. Use a fake setup command:
   - `python -c "open('marker','w').write('x')"` for success; assert the marker exists before the baseline log is written, or that setup is logged before the baseline;
   - `python -c "import sys; sys.exit(3)"` for failure, which escalates `setup_failed` with the exact summary;
   - a sleep longer than a small `setup_timeout_s` for the timeout;
   - a planted secret env var that is absent from what setup sees (the setup command writes `os.environ` keys to a file);
   - `retry` after a failure reruns setup;
   - the baseline test command `definitely-not-a-program-xyz` escalates `cmd_not_found`;
   - a check task with `check_cmd` `definitely-not-a-program-xyz` escalates `cmd_not_found` with `attempts` unchanged.
2. Implement the code to make them pass.
3. Run tests/run, then the full suite, then commit `Run a setup command in each run worktree and stop when a command can't run`.

### Task 3: Show the setup command, and docs

**Files:**
- `src/phil/ui/plan_view.py` (`render_plan` gains `setup_cmd` and `setup_cmd_source`)
- `src/phil/chat/controller.py`:
  - `_show_plan` passes the setup command;
  - `_detection_root` also exports when `config.project.setup_cmd is None`;
  - `_quick` adds the `· setup:` part
- `tests/live/bench/harness.py`: `_quick_plan_for` mirrors the new `_detection_root` condition
- `README.md`, under Test command or a new "Dependencies" subsection:
  - setup_cmd, its detection table, `""` to turn it off, and setup_timeout_s;
  - "a command that can't run stops the run"
- `docs/superpowers/plans/2026-10-01-phil-m3b-followups.md`: mark the exit-127 item as resolved by this work
- Tests: `tests/ui/test_plan_view.py`, `tests/chat/test_quick_flow.py`, and the harness offline test

Steps (test-driven):
1. Write failing tests:
   - the plan view shows `setup: npm ci (detected)`;
   - the quick line includes `· setup: npm ci` before `· tests:` when a Node fixture with `package-lock.json` is committed;
   - a Python repo shows no setup part.
2. Implement the changes to make them pass.
3. Run tests/chat, tests/ui and tests/live/bench offline, then the full suite, then commit `Show the setup command at approval and when a quick change starts`.
