# Phil: Dependencies in Run Worktrees

**Status:** approved in conversation, 2026-10-01.

## 1. Problem

Each run works in a fresh git worktree, which is a clean checkout of the base commit. Gitignored dependencies such as `node_modules` aren't there, so Node test and check commands fail.

A live quick run in a Vite project showed this:
- the baseline log reads `sh: vitest: command not found` (exit 127);
- the gates only compare against the baseline, so "fails the same before and after" passed;
- the run completed with nothing tested.

Python projects are unaffected, because `uv run` builds its own environment.

## 2. Decisions (user, 2026-10-01)

| Topic | Decision |
|---|---|
| Dependencies | A per-repo setup command runs once in each new worktree, before the baseline. It's detected from the lockfile when not configured. |
| Can't run | A command that exits 127 ("command not found") stops the run with a clear message. It's never treated as a pass, and never burns retry attempts. |
| Preflight | The programs of the setup, test and check commands are checked on PATH before a run is launched. |

## 3. Design

### 3.1 Setup command

- **Config:**
  - `[project] setup_cmd: str | None`. `None` means detect. An empty string means no setup.
  - `[project] setup_timeout_s: int`, default `600`, must be `> 0`.
- **Detection:** `phil.repo_detect.detect_setup_cmd(root)` returns a command only when `root/package.json` exists. Lockfiles are checked in this order:

  | Lockfile | Command |
  |---|---|
  | `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` |
  | `yarn.lock` | `yarn install --frozen-lockfile` |
  | `bun.lockb` or `bun.lock` | `bun install --frozen-lockfile` |
  | `package-lock.json` | `npm ci` |

  With no lockfile it returns `None`, because Phil never runs a plain `npm install` on its own. Python repos have no default.
- **Effective command:** `phil.chat.approval.effective_setup_cmd(config, root) -> (cmd | None, source)`. The source is `"config"`, `"detected"` or `"none"`. The engine and the chat both use it: the engine detects in the run's worktree, and the chat detects in the base-commit snapshot, which is the same tree.

### 3.2 Engine

- **`setup` node, before the baseline:**
  - If there's a setup command, run it in the worktree with `run_command`, the `child_env` environment (no keys) and `setup_timeout_s`. Write the output to a `setup` log artifact.
  - If it exits non-zero or times out, escalate and don't run the baseline:
    - reason `setup_failed`;
    - options `["retry", "abort"]`, `resume_to="setup"`;
    - summary `` Setup command `<cmd>` failed (exit <n>); see the setup log. `` or `` Setup command `<cmd>` timed out after <s>s. ``;
    - `log`: the log path.
  - `retry` reruns the setup node, setup and then baseline.
- **Exit codes:** `TestReport` gains `exit_code: int | None = None`, filled by `run_tests`.
- **Baseline can't run:** if the baseline report's `exit_code == 127`, escalate with reason `cmd_not_found`, options `["retry", "abort"]` and `resume_to="setup"`. The summary is:

  ``` `<test_cmd>` couldn't run: <first non-empty output line>. Dependencies may be missing in the run's worktree — set [project] setup_cmd (e.g. npm ci) — or the program isn't on PATH. ```
- **Check can't run:** in `verify`, a check-mode task's `run_check` result with `exit_code == 127` escalates the same way, using the check command and `resume_to="implement"`. It doesn't count as a failed attempt.

### 3.3 Before launch

- `launch_problems` gains program checks for the effective setup command, the effective test command and each check command. For each one, take the first word (`shlex.split`):
  - a bare name (no `/`) must be found by `shutil.which`;
  - a path containing `/` is skipped (the existing containment checks cover it).

  The problem text is ``` `npm` (from the setup command `npm ci`) isn't on PATH for Phil's runs ```.
- The quick path already treats any launch problem as a fall back to full planning, so a missing program shows at approval.

### 3.4 What the user sees

- **The approval plan view** shows `setup: <cmd>` above the test command line when there is one, with ` (detected)` when detected.
- **The quick-start line** becomes `Quick change: {id} {desc} · setup: {setup_cmd} · tests: {test_cmd}`. Each part appears only when set.
- **The chat's detection root:** `_detection_root` also exports the snapshot when `config.project.setup_cmd is None`, so detection sees the base commit.

## 4. Testing (offline)

- `detect_setup_cmd` for each lockfile, no lockfile, and no `package.json`.
- `effective_setup_cmd`: config wins, `""` disables, detection otherwise.
- Engine, using a fake setup command such as `python -c` in the test repo:
  - setup runs before the baseline;
  - a failure escalates `setup_failed` and `retry` reruns it;
  - a timeout escalates;
  - keys never reach setup (the env has no secret names);
  - a baseline exit of 127 escalates `cmd_not_found`;
  - a check exit of 127 escalates without using an attempt.
- `launch_problems`: a missing program is reported and a present one isn't.
- Chat: the plan view's setup line, and the quick line with setup.

## 5. Out of scope

- Caching dependencies across runs (e.g. a shared store).
- Python environments other than uv.
- Installing the toolchain itself.
