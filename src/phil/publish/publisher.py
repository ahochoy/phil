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
