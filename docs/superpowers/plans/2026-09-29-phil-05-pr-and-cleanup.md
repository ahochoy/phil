# Phil 5 — Pull Requests and Cleanup After Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a run completes Phil raises the pull request (asked first), and after the PR merges Phil notices, cleans up the run, and appends a learnings entry.

**Architecture:** A `phil.publish` package holds a `Publisher` protocol with a `gh`-CLI implementation (`GhPublisher`) and a `FakePublisher`, a code-rendered PR body, the learnings writer, and a service layer (`publish_run`, `sweep_prs`) shared by the CLI and the chat. Today's `phil clean` body becomes `phil.run.cleanup.clean_run`, reused by merge cleanup. The run record gains base-branch and PR columns.

**Tech Stack:** Python 3.14, uv, typer, rich, prompt_toolkit, SQLite, git, the `gh` CLI (subprocess only).

**Spec:** `docs/superpowers/specs/2026-09-29-phil-05-pr-and-cleanup-design.md`

## Global Constraints

- Phil never handles a GitHub token: all host access goes through the `gh` CLI (subprocess) or `git push`; never read `.env` or print credentials.
- `gh` and `git push` run from the target repo root in Phil's own process — never inside an agent or a worktree tool.
- Tests never touch the network or the real `gh`: an autouse fixture makes `phil.publish.make_publisher` return an unavailable `FakePublisher`; tests that publish inject their own `FakePublisher`; push tests use a local bare repository as `origin`.
- Publishing is never automatic; the chat asks `Open a PR for <run> → <base>? [y / n] › `, the CLI needs `phil pr <run>`.
- A publish or merge-check failure never changes the run's state and never fails the run; it prints one line saying what failed and how to fix it.
- `phil.cli.main`, `phil.agents.invoke`, `phil.packets`, `phil.chat.*` and `phil.publish.*` must not import langgraph/langchain/deepagents at module level.
- Escape user/agent/`gh` text printed through rich with `escape`.
- Merge-check throttle: 300 seconds per run (`pr_checked_at`); chat check interval: 300 seconds; `gh` subprocess timeout: 60 seconds.
- Learnings file: `~/.phil/projects/<slug>/learnings.md` (i.e. `ProjectPaths.project_dir / "learnings.md"`).
- Commit messages end with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

- Modify `src/phil/store/db.py` — migrations for the new `runs` columns.
- Modify `src/phil/store/runs.py` — `RunRecord` fields, `_UPDATABLE`, `create_run(base_branch=)`.
- Modify `src/phil/run/launch.py` — `prepare_run` records `info.branch`.
- Create `src/phil/publish/__init__.py` — exports.
- Create `src/phil/publish/publisher.py` — `Publisher`, `PullRequest`, `PublishError`, `GhPublisher`, `FakePublisher`, `make_publisher`.
- Create `src/phil/publish/pr_body.py` — `pr_title`, `render_pr_body`, `find_pr_template`.
- Create `src/phil/publish/learnings.py` — `learnings_entry`, `append_learnings`.
- Create `src/phil/publish/service.py` — `publish_run`, `sweep_prs`, `PrChange`.
- Create `src/phil/run/cleanup.py` — `clean_run`, `CleanError`.
- Modify `src/phil/cli/main.py` — `phil pr`, `phil clean` via `clean_run`, `phil clean --merged`, sweeps in `phil runs` / `phil show`.
- Modify `src/phil/chat/controller.py` — `confirm_pr` stage, publish job, PR monitor timer, notices.
- Modify `src/phil/ui/runs_view.py` — PR column/marker.
- Modify `tests/conftest.py` — autouse `no_real_gh` fixture.
- Docs: `README.md`, `docs/superpowers/specs/2026-09-23-phil-v1-design.md` (§11 pointer), `docs/superpowers/plans/2026-09-29-phil-05-followups.md`.

---

### Task 1: Run record gains base branch and PR fields

**Files:**
- Modify: `src/phil/store/db.py` (append to `MIGRATIONS`)
- Modify: `src/phil/store/runs.py`
- Modify: `src/phil/run/launch.py:17-33`
- Test: `tests/store/test_runs_pr_fields.py` (create), `tests/run/test_launch.py` (add one test)

**Interfaces:**
- Produces: `RunRecord.base_branch: str | None`, `.pr_url: str | None`, `.pr_number: int | None`, `.pr_state: str | None`, `.pr_checked_at: str | None` (all default `None`); `update_run` accepts `pr_url`, `pr_number`, `pr_state`, `pr_checked_at`, `base_branch`; `create_run(..., base_branch: str | None = None)`; `prepare_run` passes `base_branch=info.branch`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_runs_pr_fields.py
from pathlib import Path

from phil.store.db import connect
from phil.store.runs import create_run, get_run, update_run


def test_new_run_records_its_base_branch(tmp_path: Path):
    conn = connect(tmp_path / "phil.db")
    run = create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=tmp_path / "wt",
                     tasks_total=1, base_branch="main")
    assert run.base_branch == "main"
    assert (run.pr_url, run.pr_number, run.pr_state, run.pr_checked_at) == (None, None, None, None)


def test_pr_fields_are_updatable(tmp_path: Path):
    conn = connect(tmp_path / "phil.db")
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=tmp_path / "wt", tasks_total=1)
    update_run(conn, "r-0001", pr_url="https://github.com/o/r/pull/12", pr_number=12, pr_state="open",
               pr_checked_at="2026-09-29T00:00:00+00:00")
    run = get_run(conn, "r-0001")
    assert (run.pr_number, run.pr_state) == (12, "open")
    assert run.base_branch is None
```

Add to `tests/run/test_launch.py`:

```python
def test_prepare_run_records_the_checked_out_branch(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    assert record.base_branch == info.branch == "main"
```

(Use the file's existing imports; add `resolve_repo`/`calc_plan` imports if missing.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q -n 0 tests/store/test_runs_pr_fields.py tests/run/test_launch.py -k "base_branch or pr_fields or checked_out"`
Expected: FAIL (`create_run() got an unexpected keyword argument 'base_branch'`).

- [ ] **Step 3: Implement**

Append to `MIGRATIONS` in `src/phil/store/db.py` (one statement per entry, like the existing `ALTER TABLE` entries):

```python
    "ALTER TABLE runs ADD COLUMN base_branch TEXT",
    "ALTER TABLE runs ADD COLUMN pr_url TEXT",
    "ALTER TABLE runs ADD COLUMN pr_number INTEGER",
    "ALTER TABLE runs ADD COLUMN pr_state TEXT",
    "ALTER TABLE runs ADD COLUMN pr_checked_at TEXT",
```

In `src/phil/store/runs.py`: add `"base_branch", "pr_url", "pr_number", "pr_state", "pr_checked_at"` to `_UPDATABLE`; add the five fields to `RunRecord` after `chat_id`, each `= None`; give `create_run` a `base_branch: str | None = None` keyword and include it in the `INSERT` column list and values. In `prepare_run` pass `base_branch=info.branch`.

- [ ] **Step 4: Run to verify they pass, then the full suite**

Run: `uv run pytest -q -n 0 tests/store/test_runs_pr_fields.py tests/run/test_launch.py` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `Record the run's base branch and pull request fields`

---

### Task 2: Publisher (gh CLI), FakePublisher and the test guard

**Files:**
- Create: `src/phil/publish/__init__.py`, `src/phil/publish/publisher.py`
- Modify: `tests/conftest.py` (autouse `no_real_gh`)
- Test: `tests/publish/__init__.py`, `tests/publish/test_publisher.py`

**Interfaces:**
- Produces:
  - `PullRequest(number: int, url: str)` (frozen dataclass)
  - `PublishError(Exception)` — message is one printable line.
  - `class Publisher(Protocol)`: `available() -> str | None`; `push(branch: str) -> None`; `create_pr(*, branch: str, base: str, title: str, body: str) -> PullRequest`; `pr_state(number: int) -> str` (`"open"|"merged"|"closed"`); `delete_remote_branch(branch: str) -> None` (absent branch is not an error).
  - `GhPublisher(repo_root: Path, *, runner: Callable[[list[str], str | None], CompletedProcess] | None = None, which: Callable[[str], str | None] = shutil.which)`.
  - `FakePublisher(*, unavailable: str | None = None, states: dict[int, str] | None = None, fail: dict[str, str] | None = None)` recording `calls: list[tuple]`, `pushed: list[str]`, `deleted: list[str]`; `create_pr` returns numbers starting at 12 with url `https://github.com/example/repo/pull/<n>`; `fail` maps a method name to a `PublishError` message it raises.
  - `make_publisher(repo_root: Path) -> Publisher` — returns `GhPublisher(repo_root)`; the one seam tests patch.

- [ ] **Step 1: Write the failing tests**

```python
# tests/publish/test_publisher.py
import subprocess
from pathlib import Path

import pytest

from phil.publish.publisher import FakePublisher, GhPublisher, PublishError, make_publisher
from tests.helpers import run_git


def completed(args, code=0, out="", err=""):
    return subprocess.CompletedProcess(args, code, out, err)


class Runner:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        return self.replies.pop(0)


def test_available_explains_a_missing_gh(git_repo: Path):
    pub = GhPublisher(git_repo, runner=Runner([]), which=lambda name: None)
    assert "gh is not installed" in pub.available()


def test_available_explains_a_logged_out_gh(git_repo: Path):
    runner = Runner([completed(["gh"], code=1, err="You are not logged into any GitHub hosts")])
    pub = GhPublisher(git_repo, runner=runner, which=lambda name: "/usr/bin/gh")
    assert "gh auth login" in pub.available()


def test_available_needs_an_origin_remote(git_repo: Path):
    runner = Runner([completed(["gh"])])
    pub = GhPublisher(git_repo, runner=runner, which=lambda name: "/usr/bin/gh")
    assert "origin" in pub.available()


def test_available_when_everything_is_set_up(git_repo: Path, tmp_path: Path):
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(git_repo, "remote", "add", "origin", str(remote))
    pub = GhPublisher(git_repo, runner=Runner([completed(["gh"])]), which=lambda name: "/usr/bin/gh")
    assert pub.available() is None


def test_push_sends_the_branch_to_origin(git_repo: Path, tmp_path: Path):
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(git_repo, "remote", "add", "origin", str(remote))
    run_git(git_repo, "branch", "phil/r-0001")
    GhPublisher(git_repo, runner=Runner([])).push("phil/r-0001")
    assert run_git(remote, "branch", "--list", "phil/r-0001").strip() == "phil/r-0001"


def test_push_failure_is_a_publish_error(git_repo: Path):
    with pytest.raises(PublishError):
        GhPublisher(git_repo, runner=Runner([])).push("phil/r-0001")  # no origin


def test_create_pr_passes_base_head_title_and_body_on_stdin(git_repo: Path):
    runner = Runner([completed(["gh"], out="https://github.com/o/r/pull/12\n")])
    pr = GhPublisher(git_repo, runner=runner).create_pr(branch="phil/r-0001", base="main", title="T", body="B")
    assert (pr.number, pr.url) == (12, "https://github.com/o/r/pull/12")
    args, stdin = runner.calls[0]
    assert args[:3] == ["gh", "pr", "create"]
    assert ["--base", "main"] == args[args.index("--base"):args.index("--base") + 2]
    assert ["--head", "phil/r-0001"] == args[args.index("--head"):args.index("--head") + 2]
    assert "--body-file" in args and stdin == "B"


def test_create_pr_failure_carries_gh_stderr(git_repo: Path):
    runner = Runner([completed(["gh"], code=1, err="a pull request already exists")])
    with pytest.raises(PublishError, match="already exists"):
        GhPublisher(git_repo, runner=runner).create_pr(branch="b", base="main", title="T", body="B")


@pytest.mark.parametrize("reply, state", [('{"state": "OPEN"}', "open"), ('{"state": "MERGED"}', "merged"),
                                          ('{"state": "CLOSED"}', "closed")])
def test_pr_state_maps_gh_json(git_repo: Path, reply: str, state: str):
    runner = Runner([completed(["gh"], out=reply)])
    assert GhPublisher(git_repo, runner=runner).pr_state(12) == state
    assert runner.calls[0][0][:4] == ["gh", "pr", "view", "12"]


def test_delete_remote_branch_tolerates_an_absent_branch(git_repo: Path, tmp_path: Path):
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(git_repo, "remote", "add", "origin", str(remote))
    GhPublisher(git_repo, runner=Runner([])).delete_remote_branch("phil/r-0001")  # no error


def test_fake_publisher_records_and_fails_on_demand():
    fake = FakePublisher(states={12: "merged"}, fail={"push": "rejected"})
    with pytest.raises(PublishError, match="rejected"):
        fake.push("phil/r-0001")
    assert fake.create_pr(branch="b", base="main", title="T", body="B").number == 12
    assert fake.pr_state(12) == "merged"


def test_tests_never_get_the_real_gh(git_repo: Path):
    assert make_publisher(git_repo).available() is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q -n 0 tests/publish/test_publisher.py`
Expected: FAIL (`ModuleNotFoundError: phil.publish`).

- [ ] **Step 3: Implement**

`src/phil/publish/publisher.py`:

```python
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from phil.git import GitError, git

GH_TIMEOUT_S = 60
_PR_NUMBER = re.compile(r"/pull/(\d+)")


class PublishError(Exception):
    """A publish step failed; the message is one printable line."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str


class Publisher(Protocol):
    def available(self) -> str | None: ...
    def push(self, branch: str) -> None: ...
    def create_pr(self, *, branch: str, base: str, title: str, body: str) -> PullRequest: ...
    def pr_state(self, number: int) -> str: ...
    def delete_remote_branch(self, branch: str) -> None: ...


Runner = Callable[[list[str], str | None], subprocess.CompletedProcess]


class GhPublisher:
    """GitHub through the `gh` CLI: gh owns authentication, so Phil never sees a token."""

    def __init__(self, repo_root: Path, *, runner: Runner | None = None,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        self.repo_root, self._which = repo_root, which
        self._runner = runner or self._run

    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, cwd=self.repo_root, input=stdin, capture_output=True, text=True,
                                  timeout=GH_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise PublishError(f"`{' '.join(args[:3])}` timed out after {GH_TIMEOUT_S}s") from exc
        except OSError as exc:
            raise PublishError(f"could not run gh: {exc}") from exc

    def _gh(self, args: list[str], stdin: str | None = None) -> str:
        result = self._runner(["gh", *args], stdin)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise PublishError(f"gh {args[0]} {args[1]} failed: {detail[-1] if detail else 'no output'}")
        return result.stdout

    def available(self) -> str | None:
        if self._which("gh") is None:
            return "gh is not installed (https://cli.github.com), so Phil can't open pull requests"
        if self._runner(["gh", "auth", "status"], None).returncode != 0:
            return "gh is not logged in; run `gh auth login`"
        try:
            git(self.repo_root, "remote", "get-url", "origin")
        except GitError:
            return "this repository has no `origin` remote"
        return None

    def push(self, branch: str) -> None:
        try:
            git(self.repo_root, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
        except GitError as exc:
            raise PublishError(f"git push failed: {str(exc).strip().splitlines()[-1]}") from exc

    def create_pr(self, *, branch: str, base: str, title: str, body: str) -> PullRequest:
        out = self._gh(["pr", "create", "--base", base, "--head", branch, "--title", title, "--body-file", "-"], body)
        url = out.strip().splitlines()[-1] if out.strip() else ""
        match = _PR_NUMBER.search(url)
        if match is None:
            raise PublishError(f"gh pr create returned no pull request URL: {url!r}")
        return PullRequest(int(match.group(1)), url)

    def pr_state(self, number: int) -> str:
        out = self._gh(["pr", "view", str(number), "--json", "state"])
        try:
            state = str(json.loads(out)["state"]).lower()
        except (ValueError, KeyError, TypeError) as exc:
            raise PublishError(f"gh pr view returned unexpected output: {out[:80]!r}") from exc
        if state not in ("open", "merged", "closed"):
            raise PublishError(f"unknown pull request state {state!r}")
        return state

    def delete_remote_branch(self, branch: str) -> None:
        try:
            if git(self.repo_root, "ls-remote", "--heads", "origin", branch).strip():
                git(self.repo_root, "push", "origin", "--delete", branch)
        except GitError as exc:
            raise PublishError(f"could not delete origin/{branch}: {str(exc).strip().splitlines()[-1]}") from exc


@dataclass
class FakePublisher:
    """Test double: records calls; `fail` maps a method name to the PublishError message it raises."""

    unavailable: str | None = None
    states: dict[int, str] = field(default_factory=dict)
    fail: dict[str, str] = field(default_factory=dict)
    calls: list[tuple] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    _next: int = 12

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise PublishError(self.fail[name])

    def available(self) -> str | None:
        return self.unavailable

    def push(self, branch: str) -> None:
        self.calls.append(("push", branch))
        self._maybe_fail("push")
        self.pushed.append(branch)

    def create_pr(self, *, branch: str, base: str, title: str, body: str) -> PullRequest:
        self.calls.append(("create_pr", branch, base, title, body))
        self._maybe_fail("create_pr")
        number, self._next = self._next, self._next + 1
        self.states.setdefault(number, "open")
        return PullRequest(number, f"https://github.com/example/repo/pull/{number}")

    def pr_state(self, number: int) -> str:
        self.calls.append(("pr_state", number))
        self._maybe_fail("pr_state")
        return self.states.get(number, "open")

    def delete_remote_branch(self, branch: str) -> None:
        self.calls.append(("delete_remote_branch", branch))
        self._maybe_fail("delete_remote_branch")
        self.deleted.append(branch)


def make_publisher(repo_root: Path) -> Publisher:
    return GhPublisher(repo_root)
```

`src/phil/publish/__init__.py`: re-export `FakePublisher, GhPublisher, PublishError, Publisher, PullRequest, make_publisher`.

Add to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def no_real_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests must never reach GitHub: the default publisher is an unavailable fake. Tests that
    # publish patch `phil.publish.publisher.make_publisher` (or pass a FakePublisher) themselves.
    from phil.publish.publisher import FakePublisher

    monkeypatch.setattr(
        "phil.publish.publisher.make_publisher", lambda repo_root: FakePublisher(unavailable="gh is disabled in tests")
    )
```

Callers elsewhere must call `publisher.make_publisher(...)` through the module (`from phil.publish import publisher as publishing; publishing.make_publisher(root)`) so the patch applies.

- [ ] **Step 4: Run to verify they pass, then the full suite**

Run: `uv run pytest -q -n 0 tests/publish/test_publisher.py` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `Add a gh-backed publisher with a fake for tests`

---

### Task 3: PR title and body

**Files:**
- Create: `src/phil/publish/pr_body.py`
- Test: `tests/publish/test_pr_body.py`

**Interfaces:**
- Consumes: `ArtifactStore(run_dir).read_plan()`, `.read_assumptions()`; `run_dir/"open_issues.json"` (a JSON list of issue dicts `{"severity", "note", "task_id"?}`, may be missing or malformed); `run_dir/"outputs"/review-*.json` and `tester-*.json` (contract JSON); `phil.store.telemetry.run_usage(conn, run_id) -> Totals` and `format_cost`; `phil.run.state.issue_line`, `dedupe_issues`.
- Produces:
  - `pr_title(plan: Plan) -> str` — `f"{plan.keyword}: {first sentence of plan.description}"`, whitespace collapsed, capped at 72 chars (cut at 71 + `…`).
  - `find_pr_template(repo_root: Path) -> str | None` — first existing of `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `docs/pull_request_template.md`; text capped at 20,000 chars.
  - `render_pr_body(*, run_id: str, run_dir: Path, totals: Totals | None, template: str | None) -> str`.

Body layout (exact headings):

```
## Action needed
- [major] MAPS-002: still failing: test_x      ← one `issue_line` per deduplicated open issue
- Assumption not confirmed: <text>             ← see rule below
None.                                          ← when both lists are empty

## What changed
<plan.description>

- [x] MAPS-001 Add subtract                     ← `task_lines(plan)`

## How it was verified
- Tests: no new failures against the base      ← or "- Tests: N still failing (see Action needed)" counting issues whose note starts with "still failing"
- Tester: <n> report(s)                        ← count of outputs/tester-*.json (omit line when 0)
- Reviewer: approve                            ← verdict of the newest outputs/review-*.json by trailing attempt number; "- Reviewer: not run" when none

## Usage
Total: 123,456 tokens · ~$0.42                 ← omitted when totals is None

## Template                                     ← only when template is not None, followed by its text unchanged

---
Opened by Phil from run r-7f3a.
```

Assumption rule: take the newest review's `assumption_resolutions`; each resolution not starting with `confirmed` (case-insensitive) is listed as `- Assumption not confirmed: <resolution>`. With no review output, every `read_assumptions()` entry whose `status == "open"` is listed as `- Assumption not confirmed: <assumption>`. All notes pass through `clean_note` (already in `phil.run.state`) so they stay on one line.

- [ ] **Step 1: Write the failing tests** — in `tests/publish/test_pr_body.py`, build a run dir with `ArtifactStore(tmp_path / "run").write_plan(calc_plan())` (from `tests.run.conftest`), then:
  - `test_title_uses_keyword_and_first_sentence` — plan description `"Add subtract. Then more."` → title starts `"CALC: Add subtract."` and has no `"Then"`; a 200-char description gives `len(title) == 72` ending in `…`.
  - `test_body_says_none_when_nothing_needs_action` — no open_issues.json, a review output `{"verdict": "approve", "issues": [], "assumption_resolutions": ["confirmed: x"], ...}` written with `write_json("outputs", "review-run-3", …)` → `"## Action needed\nNone."` in body; `"- Reviewer: approve"`; `"- Tests: no new failures against the base"`.
  - `test_body_lists_open_issues_and_unconfirmed_assumptions` — open_issues.json with two duplicate issues + one `"still failing: test_a"` issue, review resolution `"issue raised: y"` → one line per unique issue, `"- Assumption not confirmed: issue raised: y"`, `"- Tests: 1 still failing (see Action needed)"`.
  - `test_body_uses_the_newest_review` — `review-run-2` says `changes`, `review-run-10` says `approve` → `"- Reviewer: approve"` (numeric, not lexical, ordering).
  - `test_body_tolerates_malformed_open_issues_json` — file contains `"{"` → body renders, `None.` in Action needed.
  - `test_body_appends_the_template_unfilled` — `template="## Checklist\n- [ ] docs"` → body contains `"## Template\n## Checklist\n- [ ] docs"` after `## Usage`.
  - `test_find_pr_template_prefers_github_dir` — create both `.github/pull_request_template.md` and `docs/pull_request_template.md` → returns the `.github` one's text; none → `None`.
  - `test_usage_line_uses_format_cost` — `Totals(tokens=1234, cost_usd=0.42, cost_source="estimated")` (from `phil.store.telemetry`) → `"Total: 1,234 tokens · ~$0.42"`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest -q -n 0 tests/publish/test_pr_body.py` → FAIL (module missing).

- [ ] **Step 3: Implement** `pr_body.py` per the layout above. Parse review/tester outputs with `json.loads` inside `try/except (OSError, ValueError)`, skipping unreadable files; sort outputs by the integer after the last `-` in the stem (`review-run-10` → 10; non-numeric → -1).

- [ ] **Step 4: Run to verify they pass, then the full suite.**

- [ ] **Step 5: Commit** — `Render pull request titles and bodies from the run's records`

---

### Task 4: `clean_run`, keeping open_issues.json, and learnings

**Files:**
- Create: `src/phil/run/cleanup.py`, `src/phil/publish/learnings.py`
- Modify: `src/phil/cli/main.py` (`clean` command body → `clean_run`)
- Test: `tests/run/test_cleanup.py`, `tests/publish/test_learnings.py`; existing `tests/cli/test_diff_clean.py` must keep passing except the one assertion updated below.

**Interfaces:**
- Produces:
  - `class CleanError(Exception)` — one printable line.
  - `clean_run(info: RepoInfo, conn: sqlite3.Connection, record: RunRecord, *, purge: bool = False, publisher: Publisher | None = None) -> None` — the current `clean` body (worktree remove/prune, local branch delete, `refs/phil/<run>/` refs, checkpoint thread, run-dir trim, `state="cleaned"`), raising `CleanError` instead of printing/exiting. The trim keeps `summary.md` **and `open_issues.json`** unless `purge`. When `publisher` is given, it calls `publisher.delete_remote_branch(record.branch)` after the local cleanup; a `PublishError` there is re-raised as `CleanError` only after the run is already marked `cleaned` (so the local cleanup is never repeated) — callers print it as a warning. The state/worker-alive refusals stay in the CLI command (they are about user intent, not mechanics), but `clean_run` asserts `record.state not in ("pending", "running", "escalated")` by raising `CleanError`.
  - `learnings_entry(*, record: RunRecord, run_dir: Path, today: str) -> str` and `append_learnings(paths: ProjectPaths, entry: str, run_id: str) -> bool` — appends unless the file already contains a line starting `## <run_id> ` (returns False then). Entry format:

```
## r-7f3a — 2026-09-29 — PR #12
Goal: CALC: Add subtract.
Assumptions confirmed: 2 · not confirmed: issue raised: y
Reviewer notes:
- [minor] CALC-001: rename helper
```

  `Goal:` uses `pr_title(plan)` (plan read from `run_dir`; if unreadable, `Goal: (plan unavailable)`). Assumptions come from the newest review output's resolutions (count those starting `confirmed`; list the rest joined by `; `, or `none`); with no review, `Assumptions: none recorded`. Reviewer notes: up to 5 deduplicated issues from the newest review output via `issue_line` (notes already cleaned by `clean_note`), or the line `Reviewer notes: none`. Separate entries with one blank line; create the file with a first line `# Phil learnings — <repo slug>` then a blank line.

- [ ] **Step 1: Write the failing tests**
  - `tests/run/test_cleanup.py` (use `finished_run` from `tests/cli/test_diff_clean.py` — import it): `test_clean_run_keeps_summary_and_open_issues` (write `open_issues.json` into the run dir first; after `clean_run`, `sorted(p.name for p in run_dir.iterdir()) == ["open_issues.json", "summary.md"]`, state `cleaned`); `test_clean_run_purge_removes_everything`; `test_clean_run_refuses_a_running_run` (`update_run` to `running` via the allowed transitions, `pytest.raises(CleanError)`); `test_clean_run_deletes_the_remote_branch` (FakePublisher → `deleted == [record.branch]`); `test_remote_deletion_failure_is_reported_after_cleaning` (FakePublisher `fail={"delete_remote_branch": "denied"}` → `CleanError` matching `denied`, and `get_run(...).state == "cleaned"`).
  - Update `tests/cli/test_diff_clean.py::test_clean_keeps_only_the_summary` to write an `open_issues.json` before cleaning and expect `["open_issues.json", "summary.md"]` when sorted (rename it `test_clean_keeps_the_summary_and_open_issues`). Other clean tests must pass unchanged.
  - `tests/publish/test_learnings.py`: entry format with a review output (confirmed count, not-confirmed list, ≤5 deduplicated notes); `Assumptions: none recorded` without a review; `append_learnings` creates the file with the header, appends a second run's entry after a blank line, and returns False (file unchanged) for a run id already present.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** Move the mechanics from `clean` in `cli/main.py` into `clean_run` (imports: `WorktreeManager, Worktree`, `git`, `GitError`, `open_checkpointer` imported inside the function to keep langgraph off module import, `ProjectPaths`, `update_run`). The CLI `clean` keeps its refusals (state, worker alive), calls `clean_run(info, conn, record, purge=purge)`, prints `CleanError` as `[phil.error]…[/]` and exits 1, and prints the same success line as today, with `(kept summary.md and open_issues.json)` when not purging.

- [ ] **Step 4: Run to verify they pass, then the full suite.**

- [ ] **Step 5: Commit** — `Share run cleanup, keep open issues, and write learnings`

---

### Task 5: `publish_run` and `phil pr`

**Files:**
- Create: `src/phil/publish/service.py`
- Modify: `src/phil/cli/main.py` (new `pr` command)
- Test: `tests/publish/test_service_publish.py`, `tests/cli/test_pr_command.py`

**Interfaces:**
- Consumes: Task 1 fields; Task 2 `Publisher`, `PublishError`, `publisher.make_publisher`; Task 3 `pr_title`, `render_pr_body`, `find_pr_template`; `run_usage`.
- Produces:
  - `class PublishRefused(Exception)` — a reason line, nothing was done.
  - `publish_run(info: RepoInfo, conn: sqlite3.Connection, record: RunRecord, publisher: Publisher, *, base: str | None = None) -> RunRecord` — refusals (raise `PublishRefused`, in this order): state is not `completed` → `"<run> is <state>; only a completed run can be published"`; `record.pr_url` set → `"<run> already has PR #<n>: <url>"`; `base or record.base_branch` is None → `"<run> started on a detached HEAD; pass --base <branch>"`; `publisher.available()` returns a reason → that reason. Then `publisher.push(record.branch)`, `publisher.create_pr(branch=record.branch, base=base_branch, title=pr_title(plan), body=render_pr_body(...))`, and `update_run(conn, run_id, pr_url=…, pr_number=…, pr_state="open", pr_checked_at=utcnow(), base_branch=base_branch)`. `PublishError` propagates unchanged and nothing is written.
  - CLI: `phil pr <run> [--base <branch>]` — prints `Opened PR #12: <url>` (url escaped) on success; `PublishRefused`/`PublishError` → `[phil.error]<line>[/]`, exit 1. Uses `publisher.make_publisher(info.root)` via the module.

- [ ] **Step 1: Write the failing tests**
  - Service (use `finished_run` for a completed run): success stores `pr_number == 12`, `pr_state == "open"`, FakePublisher `pushed == [record.branch]`, create_pr call carries `base == "main"` and a title starting `"CALC:"`; each refusal (a failed run via `failed_run`, already published, `base_branch=None` via `conn.execute("UPDATE runs SET base_branch = NULL …")`, `FakePublisher(unavailable="gh is not logged in; run `gh auth login`")`) raises `PublishRefused` and makes no publisher push/create calls; `--base` overrides a missing base branch and is stored; a push failure (`fail={"push": "rejected"}`) raises `PublishError` and leaves `pr_url` None; a create failure after a successful push also leaves `pr_url` None (re-running is safe: `git push` of the same ref is idempotent).
  - CLI (`monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)`): success output contains `Opened PR #12`; the default (unpatched) test publisher gives exit 1 with `gh is disabled in tests`.

- [ ] **Step 2–4:** red, implement, green, full suite.

- [ ] **Step 5: Commit** — `Add phil pr to push a run and open its pull request`

---

### Task 6: Merge detection (`sweep_prs`) in the CLI

**Files:**
- Modify: `src/phil/publish/service.py`
- Modify: `src/phil/cli/main.py` (`runs`, `show`, `clean --merged`)
- Modify: `src/phil/ui/runs_view.py` (PR marker)
- Test: `tests/publish/test_service_sweep.py`, `tests/cli/test_pr_command.py` (sweep cases), `tests/test_ui.py` or the existing runs-view test file (marker)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class PrChange: run_id: str; number: int; kind: str; detail: str = ""` — `kind` is `"merged"` (cleaned), `"closed"`, `"cleanup_failed"` (merged but `clean_run` raised; `detail` is the error line), or `"warning"` (cleaned, but the remote branch deletion failed; `detail` is the line).
  - `sweep_prs(info: RepoInfo, conn: sqlite3.Connection, publisher: Publisher, *, now: datetime | None = None, min_interval_s: float = 300.0, force: bool = False) -> list[PrChange]` — candidates: runs with `pr_state == "open"` whose `pr_checked_at` is None or older than `min_interval_s` (ignored when `force`), plus runs with `pr_state == "merged"` and `state != "cleaned"` (a cleanup that failed earlier; always retried). If `publisher.available()` gives a reason, return `[]` without touching anything. For each open candidate: `pr_state(number)` (a `PublishError` skips that run silently this time, leaving `pr_checked_at` unchanged); always write `pr_checked_at`; `open` → nothing else; `closed` → `update_run(pr_state="closed")` and a `closed` change; `merged` → `update_run(pr_state="merged")`, then `append_learnings(paths, learnings_entry(...), run_id)` (before trimming), then `clean_run(..., publisher=publisher)` → `merged` change; `CleanError` from the remote-branch step (run already `cleaned`) → `warning`; any other `CleanError` → `cleanup_failed`. A run whose worker is alive (`is_worker_alive`) is skipped.
  - `change_line(change: PrChange) -> str` (plain text, callers escape): `merged` → `"r-7f3a merged (#12); cleaned up."`; `closed` → `"r-7f3a's PR #12 was closed without merging; `phil clean r-7f3a` removes it."`; `cleanup_failed` → `"r-7f3a merged (#12), but cleanup failed: <detail>"`; `warning` → `"r-7f3a merged (#12); cleaned up, but <detail>"`.
  - CLI: `phil runs` and `phil show` call `sweep_prs` first (via `publisher.make_publisher(info.root)`) and print each `change_line` (muted for merged, warn for the rest) before their normal output; a sweep exception is caught and ignored (logged at debug) so the command still works offline. `phil clean --merged` (run id optional when `--merged`; exactly one of them required) calls `sweep_prs(..., force=True)` and prints each change, or `No merged pull requests to clean up.`
  - `render_runs` shows `PR #12` (open), `merged #12`, `closed #12` in the row's status area when `pr_number` is set.

- [ ] **Step 1: Write the failing tests** — sweep with FakePublisher and `finished_run` records published through `publish_run` first: merged → state `cleaned`, `pr_state == "merged"`, learnings file contains `## <run_id> `, FakePublisher `deleted == [branch]`, one `merged` change; closed → `pr_state == "closed"`, state still `completed`, one `closed` change, second sweep (with `force=True`) yields nothing new for it (closed runs aren't candidates); open → no change, `pr_checked_at` updated; throttle → a run checked 10 s ago is not queried (no `pr_state` call) unless `force`; `pr_state` raising → no change, `pr_checked_at` unchanged; unavailable publisher → `[]` and no calls; failed cleanup (monkeypatch `phil.publish.service.clean_run` to raise `CleanError("boom")`) → `cleanup_failed`, and a later sweep retries it and cleans (learnings not duplicated); remote-branch failure → `warning` change and state `cleaned`. CLI: `phil runs` with a patched publisher whose PR is merged prints `merged (#12); cleaned up.`; `phil clean --merged` output; `phil clean` with neither argument exits non-zero with a usage line; runs view marker test.

- [ ] **Step 2–4:** red, implement, green, full suite.

- [ ] **Step 5: Commit** — `Notice merged pull requests and clean their runs up`

---

### Task 7: Chat — ask to open the PR, and watch for merges

**Files:**
- Modify: `src/phil/chat/controller.py`
- Test: `tests/chat/test_controller_pr.py` (create; follow the construction helpers used by the existing controller run tests, e.g. `tests/chat/test_controller_run.py`)

**Interfaces:**
- Consumes: `publish_run`, `PublishRefused`, `PublishError`, `sweep_prs`, `change_line`, `publisher.make_publisher` (through the module), `get_run`.
- Produces (controller behaviour):
  - New stage `"confirm_pr"` with prompt `PROMPTS["confirm_pr"] = "Open a PR? [y / n] › "`. After the completion notice for a `completed` run (in `_on_run_done`, after `_run_notice`), if the run record has `base_branch` and no `pr_url`, print `Open a PR for <run> → <base>?` and set stage `confirm_pr`, remembering the run id in `self._pr_offer`. Not offered for other states or when the record is missing.
  - Input at `confirm_pr`: `y`/`yes` → print `Opening a PR for <run>…`, set step `Opening PR`, run a side job (`generation=-1`) `pr_opened` that calls `publish_run` with the job's own connection and `publisher.make_publisher(self.info.root)`, returning `{"run_id", "number", "url"}`; the stage goes back to `idle` immediately (the chat stays usable). `n`/`no`/empty → print `No PR. \`phil pr <run>\` opens one later.` and go `idle`. Any other text → the same "No PR" line, then the text is treated as a new goal (`_begin_goal(text)`), so a goal typed at the question isn't lost.
  - Events: `pr_opened` → `Opened PR #12: <url>`; a job failure for `pr_opened` (`PublishRefused`/`PublishError` surface through the `failed` event's `error` text `"<Type>: <line>"`) → one warn line `Couldn't open the PR: <line>` followed by `Fix that, then \`phil pr <run>\`.` — strip the `"PublishRefused: "`/`"PublishError: "` prefix. Register handlers the same way existing job kinds (`btw_answer`/`btw_failed`) are handled.
  - PR monitor: when the chat starts (in `run()`, before the prompt loop) and then every `PR_CHECK_INTERVAL_S = 300` seconds, a daemon timer thread submits a side job `pr_changes` that runs `sweep_prs` (its own connection) and returns `{"changes": [asdict(c) …]}`; the main thread prints each `change_line` (muted for `merged`, warn otherwise) and refreshes the parked count. The timer thread only submits jobs (never prints, never touches the main connection), uses an `Event` so quitting stops it, and skips a tick while a previous `pr_changes` job is still running. A failed `pr_changes` job is logged to the chat's `phil.log` via the existing logger and never printed. The constructor takes `pr_check_interval_s: float = PR_CHECK_INTERVAL_S` and `start_pr_monitor: bool = True` so tests can disable or shorten it.
  - `/help` mentions nothing new (the question is self-explanatory).

- [ ] **Step 1: Write the failing tests** (patch `phil.publish.publisher.make_publisher` to return a shared `FakePublisher`; drive the controller with the existing fake IO and inline job runner used by other controller tests):
  - completion of a completed run with a base branch prints `Open a PR for r-… → main?` and the stage is `confirm_pr`;
  - `y` → after draining events, `Opened PR #12:` printed and the run record has `pr_number == 12`;
  - `n` → `No PR.` printed, stage `idle`, no publisher calls;
  - typing a goal at the question → `No PR.` then intake starts for that goal (stage `intake`);
  - unavailable publisher → `Couldn't open the PR: gh is disabled in tests` and `phil pr` hint;
  - a failed/stopped run's completion doesn't ask;
  - a `pr_changes` job with a merged PR prints `merged (#12); cleaned up.`;
  - the monitor thread: with `pr_check_interval_s=0.05`, at least two `pr_changes` jobs are submitted within 0.5 s and none after `close()`/quit (use the real `threading` timer with the fake IO's `submit` recording calls).

- [ ] **Step 2–4:** red, implement, green, full suite.

- [ ] **Step 5: Commit** — `Offer to open the PR in the chat and report merges`

---

### Task 8: Docs and follow-ups

**Files:**
- Modify: `README.md` — a "Pull requests and cleanup" section: needs `gh` installed and logged in (`gh auth login`) and an `origin` remote; the chat asks after a completed run; `phil pr <run> [--base <branch>]`; merge noticed by the chat (every 5 minutes), `phil runs` and `phil show`; what cleanup removes and keeps (`summary.md`, `open_issues.json`, telemetry); `phil clean <run>` and `phil clean --merged`; learnings at `~/.phil/projects/<slug>/learnings.md` (nothing reads it yet).
- Modify: `docs/superpowers/specs/2026-09-23-phil-v1-design.md` — in §11's "Raise the PR and clean up after merge" row, append `→ done in plan 5 (docs/superpowers/specs/2026-09-29-phil-05-pr-and-cleanup-design.md)`.
- Modify: `docs/superpowers/plans/2026-09-23-phil-01-followups.md` — under "MVP lifecycle (after plan 4)", add a line: `Done in plan 5 except full project memory (spec #2) and non-GitHub hosts.`
- Create: `docs/superpowers/plans/2026-09-29-phil-05-followups.md` — deferred: project memory (agents reading learnings.md), PR template filling, non-GitHub hosts (GitLab etc., with provider-agnostic models), squash option, auto-publish config switch, `learnings.md` in-repo option, anything the reviews defer.
- Test: none new (docs only); run the full suite.

- [ ] **Step 1:** write the docs.
- [ ] **Step 2:** `uv run pytest -q` → PASS.
- [ ] **Step 3: Commit** — `Document pull requests and cleanup after merge`

Live check (user, after the plan): in a scratch repo with a GitHub remote, run a small goal in the chat, answer `y`, see the PR, merge it on GitHub, wait for (or trigger with `phil runs`) `merged (#N); cleaned up.`, and read `~/.phil/projects/<slug>/learnings.md`.
