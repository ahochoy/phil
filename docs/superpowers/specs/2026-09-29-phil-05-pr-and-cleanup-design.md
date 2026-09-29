# Phil 5 — Pull Requests and Cleanup After Merge

**Status:** approved in conversation 2026-09-29.
**Scope:** plan 5 — the MVP lifecycle from the v1 spec (§1 "MVP beyond v1", §11): after a run completes, Phil raises the pull request; after the PR merges, Phil cleans up and records light learnings. Deferred: real project memory (how agents read learnings — spec #2), filling in PR templates section by section, hosts other than GitHub (with provider-agnostic models, before public release), squashing.

## 1. Problem

A run ends at a reviewed `phil/<run-id>` branch in a worktree under `~/.phil`. The user then pushes it, opens the PR, merges, and runs `phil clean` by hand — and nothing learned in the run survives beyond `summary.md`. The v1 teammate principles say Phil should close the loop itself: raise the PR, notice the merge, clean up, keep what is worth keeping.

## 2. Decisions (user, 2026-09-29)

| Topic | Decision |
|---|---|
| Scope | PR + cleanup + light learnings (a per-project notes file). Full project memory is spec #2. |
| PR trigger | Phil asks at completion (`Open a PR …? (y/n)` in the chat); `phil pr <run>` from the CLI. Never automatic. |
| Cleanup | Phil detects the merge (PR state via `gh`) and cleans up, reporting it; `phil clean <run>` stays for manual/abandoned runs. |
| History | Keep per-task commits; the user chooses squash/merge on GitHub. |
| Host access | The `gh` CLI behind a small `Publisher` interface; Phil never handles a GitHub token. |
| Learnings location | `~/.phil/projects/<slug>/learnings.md` (not in the repo). |

## 3. Design

### 3.1 Run record

`runs` gains `base_branch` (the branch checked out when the run started; `NULL` for a detached HEAD, and for runs created before this plan), `pr_url`, `pr_number`, `pr_state` (`open` / `merged` / `closed`, `NULL` before publishing) and `pr_checked_at`. The run state machine is unchanged: publishing doesn't change `completed`; cleanup moves it to `cleaned` as today.

### 3.2 Publisher

- `Publisher` protocol: `available() -> str | None` (why not, e.g. "gh is not installed", "gh is not logged in — run `gh auth login`", "no `origin` remote"), `create_pr(branch, base, title, body) -> PullRequest(number, url)`, `pr_state(number) -> str` (`open`/`merged`/`closed`), `delete_remote_branch(branch)`.
- `GhPublisher(repo_root)` runs `gh` as a subprocess in the repo root (never inside a worktree agent), with a timeout; parsing uses `--json`. Pushing uses `git push origin phil/<run-id>` from the repo root.
- Tests use a `FakePublisher`; push tests use a local bare repository as `origin`. No test touches the network.

### 3.3 Raising the PR

- **When:** the chat's completion notice for a `completed` run with a base branch asks `Open a PR for r-7f3a → main? (y/n)`; `y` publishes on a job thread and posts the result. `phil pr <run>` does the same from the CLI. Refused (one clear line, no change) for runs that aren't `completed`, runs already published, runs with no base branch (detached HEAD — `phil pr <run> --base <branch>` supplies one), and when `Publisher.available()` gives a reason (printed with its fix).
- **Steps:** push the branch; create the PR (`--base <base_branch> --head phil/<run-id>`); store `pr_url`, `pr_number`, `pr_state="open"`; print `Opened PR #12: <url>`. A failed push or create prints the error line; the run is unchanged and the command can be repeated.
- **Title:** the plan's goal, one line, capped at 72 characters.
- **Body** (code-rendered from the run's records — no model call):
  1. `## Action needed` — open issues (deduplicated, from `open_issues.json`), still-open assumptions, and failing checks; or `None.`
  2. `## What changed` — the goal, then one line per task (id, title, status).
  3. `## How it was verified` — gates, tester and reviewer outcomes per task.
  4. `## Usage` — the run's totals line (tokens, cost with `~`/`?` markers).
  5. A footer naming the run id.
  If the repo has a PR template (`.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`, or `docs/pull_request_template.md`), its text is appended below Phil's sections under `## Template`, unfilled.

### 3.4 Noticing the merge

- The PR's state is the only reliable merge signal (squash merges break git ancestry). Phil checks runs whose `pr_state` is `open`:
  - from the chat's `RunWatcher` while a chat is open (at most every 5 minutes), and
  - when `phil`, `phil runs` or `phil show` starts, skipping runs checked in the last 5 minutes (`pr_checked_at`).
  Checks run in the background of the chat and never block its prompt; a failed check is logged and retried next time.
- `merged` → clean up (§3.5) and report `r-7f3a merged (#12); cleaned up.` (in the chat, or on stdout for the CLI commands).
- `closed` without merge → record `pr_state="closed"` and say `r-7f3a's PR #12 was closed without merging; phil clean r-7f3a removes it.` once. No automatic cleanup.

### 3.5 Cleanup

- The body of today's `phil clean` becomes a function `clean_run(...)` shared by the CLI and merge detection. It removes the worktree, deletes the local `phil/<run-id>` branch and `refs/phil/<run-id>/*`, deletes the run's checkpoint thread, and trims the run directory, keeping `summary.md` **and `open_issues.json`** (unless `--purge`). Telemetry rows stay.
- After a merge it also deletes the remote branch if it still exists (GitHub may have deleted it already; a missing branch is fine).
- `phil clean --merged` checks and cleans every merged run. `phil clean <run>` keeps its confirmation behaviour for runs that were never merged.

### 3.6 Light learnings

On a merge cleanup, before trimming the run directory, Phil appends one entry to `~/.phil/projects/<slug>/learnings.md`:

```
## r-7f3a — 2026-09-29 — PR #12
Goal: <goal>
Assumptions confirmed: … / still open: …
Reviewer notes: <up to 5, deduplicated, one line each>
```

Nothing reads this file yet; spec #2 decides how agents use it. Parked items stay in the database as they are.

## 4. Testing

- `GhPublisher` against a fake `gh` runner (argument lists, JSON parsing, `available()` reasons); push against a local bare remote.
- `phil pr`: refusals, success path storing PR fields, failures leaving the run unchanged; PR body rendering (sections, `None.`, template appended).
- Merge detection: watcher and CLI-start checks with a `FakePublisher` (open → merged → cleaned; closed → one notice; 5-minute throttle).
- `clean_run`: keeps `summary.md` and `open_issues.json`; remote branch deletion tolerant of absence.
- Chat: completion notice asks, `y` publishes on a job and prints the result, `n` does nothing.
- Learnings entry format.
- Live (user): real run → `y` → PR on GitHub → merge → cleanup reported.
