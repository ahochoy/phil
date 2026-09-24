# Plan 1 (Foundation): Follow-ups for Later Plans

Findings from plan 1's task reviews and final review that were deliberately deferred. Each names the plan that should pick it up. Items marked **before plan 2** should land at the start of plan 2, before anything builds on them.

## Before plan 2

- **`GitError` shadows `BaseException.args`** (`src/phil/git.py`). Assigning `self.args = list(args)` coerces to a tuple of the elements, so pickling fails (`TypeError` on unpickle) and `repr()` drops `returncode`/`stderr`. Store the command as `self.command` and call `super().__init__(str(self))`.
- **Database schema versioning** (`src/phil/store/db.py`). Add `PRAGMA user_version` plus an ordered migration list run by `connect()`, before plan 2 or 3 adds the first column. A real database already exists at `~/.phil/projects/phil-8d83795c/`.
- **Child-process environment** (`src/phil/workspace/shell.py`). Commands inherit the full environment, including provider API keys, so agent-written test code can read them. **Decided (2026-09-23):** remove secret-looking variables by default; names listed in `[shell] pass_env` in `phil.toml` pass through (spec §9). Implement in `run_command` (pass a filtered `env=`) plus a `ShellConfig.pass_env: list[str]` field, with tests. This matters more than allowlist tuning, because running `pytest` already executes agent-written code (so `pytest -p <plugin>` and `--rootdir=/` are not meaningful boundaries).
- **Contract field descriptions** (`src/phil/contracts/`). Add `Field(description=...)` when contracts become LLM `response_format`s; descriptions reach the model, docstrings do not. Check that OpenRouter providers' strict mode accepts `pattern`, `maxLength`, `minItems`. Consider `schema_version: Literal[1]`.

## Plan 3 (run graph)

- **Run state validation.** `update_run` accepts any `state` string; the state machine should enforce `RunState`.
- **Empty commits.** `WorktreeManager.commit_all` raises `GitError` when there is nothing to commit; the commit node must handle it.
- **Commit policy in worktrees.** Decide on the user's hooks and `commit.gpgsign` (e.g. `-c commit.gpgsign=false`, possibly `--no-verify`).
- **Test-file globs.** `ProjectConfig.test_globs` uses `fnmatch` (`*` matches `/`; `test_*.py` only matches at repo root). The red/green gates must choose whole-path or basename matching.
- **Path parsing with `-z`.** `changed_files` uses `--name-only` without `-z`, so non-ASCII paths come back quoted.
- **Assumption ids.** Ledger entries need stable `A-###` ids so `Review.assumption_resolutions` can reference them (spec §8).
- **Dependencies.** Remove prototype-only dependencies (postgres checkpointer, psycopg, python-frontmatter, dotenv, deepagents-backends) and add `langgraph-checkpoint-sqlite`.
- **Missing-file reads.** Add tests for `ArtifactStore.read_plan()`/`read()` when the file does not exist.

## Plan 4 (chat and interface)

- **Dirty-file parsing.** `resolve_repo` parses `git status --porcelain` with `line[3:]`, which mis-parses renames and quoted paths; switch to `--porcelain=v2 -z` before showing the dirty-file warning.
- **Read-only commands create state.** `phil runs` and `phil parked` create `phil.db` as a side effect; show the empty state when the database does not exist.

## MVP lifecycle (after plan 4)

Raised by the user on 2026-09-23. Spec §1 "Teammate principles" and "MVP beyond v1".

- **Phil raises the PR.** After `finish`, a publisher step pushes `phil/<run-id>` and opens a PR. The description is concise and clearly organized: what changed and why, how it was verified (gates, tester, reviewer), and a clearly marked section for any action the reviewer must take. It follows the target repo's PR template and conventions when present.
- **Phil cleans up after merge.** A command (e.g. `phil done <run-id>`, or automatic when Phil sees the PR merged) that: records learnings (confirmed assumptions, parked items, reviewer notes) to project memory; removes the worktree, the local run branch, and run scratch files; and keeps only what is worth keeping. No manual commands for the user.
- **No permission gaps.** Cleanup runs from Phil's own process against state it owns (`~/.phil`, `phil/<run-id>` branches), not from inside a worktree-bound agent, so it cannot be blocked the way agents stuck in a worktree often are.
- **Needs first:** project memory design (spec #2 area), a GitHub publisher (auth, `gh` or API), and deciding the fate of the run's commit history (squash or keep per-task commits).

## Minor

- `truncate_output`: enforce a minimum `max_lines`, keep output within `max_lines`, and put the real log path in the omission marker.
- Parked ids use `COUNT(*) + 1`; switch to the max numeric suffix if deletion is ever added.
- `phil schema` writes `phil-schemas/` to the current directory by default; consider gitignoring it or defaulting elsewhere.
- `ArtifactStore.read_plan` builds its path directly instead of through `_file()` (fixed literal, not exploitable).
