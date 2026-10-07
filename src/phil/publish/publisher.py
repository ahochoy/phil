import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from phil import platform
from phil.git import GitError, git

GH_TIMEOUT_S = 60  # gh calls and `git ls-remote`
GIT_PUSH_TIMEOUT_S = 600  # `git push` (publishing, and deleting the remote branch) can move a lot of data
_PR_NUMBER = re.compile(r"/pull/(\d+)")


class PublishError(Exception):
    """A publish step failed; the message is one printable line."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str


@dataclass(frozen=True)
class PrInfo:
    """A pull request's state (`open`/`merged`/`closed`) and the head branch/commit it points at."""

    state: str
    head_ref: str
    head_oid: str


class Publisher(Protocol):
    def available(self) -> str | None: ...
    def push(self, branch: str) -> None: ...
    def create_pr(self, *, branch: str, base: str, title: str, body: str) -> PullRequest: ...
    def find_pr(self, branch: str) -> PullRequest | None: ...
    def pr_info(self, url: str) -> PrInfo: ...
    def delete_remote_branch(self, branch: str, expected_oid: str | None) -> bool: ...


Runner = Callable[[list[str], str | None], subprocess.CompletedProcess]


def _last_line(result: subprocess.CompletedProcess) -> str:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return detail[-1] if detail else "no output"


def unattended_env(repo_root: Path) -> dict[str, str]:
    """The environment for gh and network git: never prompt on the terminal (Phil may be running
    unattended in the chat), so a missing credential fails fast instead of hanging.

    Batch-mode ssh is only a default: a user's own `GIT_SSH_COMMAND`, `GIT_SSH` or the repo's
    `core.sshCommand` (e.g. a per-account key) is left in charge.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    if not (env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH") or _core_ssh_command(repo_root)):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def _core_ssh_command(repo_root: Path) -> str:
    try:
        return git(repo_root, "config", "core.sshCommand").strip()
    except GitError:  # unset (exit 1), or no git: nothing configured
        return ""


class GhPublisher:
    """GitHub through the `gh` CLI: gh owns authentication, so Phil never sees a token.

    `runner` runs gh and `git_runner` runs the network git commands (push, ls-remote); both
    default to a subprocess with a timeout and terminal prompts turned off.
    """

    def __init__(self, repo_root: Path, *, runner: Runner | None = None, git_runner: Runner | None = None,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        self.repo_root, self._which = repo_root, which
        self._runner = runner or self._run
        self._git_runner = git_runner or self._run

    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        timeout = GIT_PUSH_TIMEOUT_S if args[:2] == ["git", "push"] else GH_TIMEOUT_S
        try:
            # A new session has no controlling terminal: nothing (ssh, a credential helper) can
            # prompt on it, whatever the user configured.
            return subprocess.run(args, cwd=self.repo_root, input=stdin, capture_output=True, text=True,
                                  timeout=timeout, env=unattended_env(self.repo_root), **platform.detach_kwargs())
        except subprocess.TimeoutExpired as exc:
            raise PublishError(f"`{' '.join(args[:3])}` timed out after {timeout}s") from exc
        except OSError as exc:
            raise PublishError(f"could not run {args[0]}: {exc}") from exc

    def _gh(self, args: list[str], stdin: str | None = None) -> str:
        result = self._runner(["gh", *args], stdin)
        if result.returncode != 0:
            raise PublishError(f"gh {args[0]} {args[1]} failed: {_last_line(result)}")
        return result.stdout

    def _git(self, *args: str) -> str:
        result = self._git_runner(["git", *args], None)
        if result.returncode != 0:
            raise PublishError(f"git {args[0]} failed: {_last_line(result)}")
        return result.stdout

    def available(self) -> str | None:
        if self._which("gh") is None:
            return "gh is not installed (https://cli.github.com), so Phil can't open pull requests"
        try:
            status = self._runner(["gh", "auth", "status"], None)
        except PublishError as exc:
            return str(exc)
        if status.returncode != 0:
            return "gh is not logged in; run `gh auth login`"
        try:
            git(self.repo_root, "remote", "get-url", "origin")
        except GitError:
            return "this repository has no `origin` remote"
        return None

    def push(self, branch: str) -> None:
        self._git("push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")

    def create_pr(self, *, branch: str, base: str, title: str, body: str) -> PullRequest:
        out = self._gh(["pr", "create", "--base", base, "--head", branch, "--title", title, "--body-file", "-"], body)
        url = out.strip().splitlines()[-1] if out.strip() else ""
        match = _PR_NUMBER.search(url)
        if match is None:
            raise PublishError(f"gh pr create returned no pull request URL: {url!r}")
        return PullRequest(int(match.group(1)), url)

    def find_pr(self, branch: str) -> PullRequest | None:
        """The pull request whose head is `branch`, or None if gh finds none (or can't look)."""
        try:
            out = self._gh(["pr", "view", branch, "--json", "number,url"])
            data = json.loads(out)
            return PullRequest(int(data["number"]), str(data["url"]))
        except (PublishError, ValueError, KeyError, TypeError):
            return None

    def pr_info(self, url: str) -> PrInfo:
        out = self._gh(["pr", "view", url, "--json", "state,headRefName,headRefOid"])
        try:
            data = json.loads(out)
            info = PrInfo(str(data["state"]).lower(), str(data["headRefName"]), str(data["headRefOid"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise PublishError(f"gh pr view returned unexpected output: {out[:80]!r}") from exc
        if info.state not in ("open", "merged", "closed"):
            raise PublishError(f"unknown pull request state {info.state!r}")
        return info

    def delete_remote_branch(self, branch: str, expected_oid: str | None) -> bool:
        """Delete `origin/<branch>` if it still points at `expected_oid` (any commit when None).

        Returns True if the branch is gone (deleted, or absent already) and False if it now
        points at another commit and was left alone. An empty `expected_oid` matches nothing, so
        the branch is left alone.
        """
        ref = f"refs/heads/{branch}"
        try:
            listing = self._git("ls-remote", "--heads", "origin", ref)
            oids = [line.split("\t")[0] for line in listing.splitlines() if line.split("\t")[1:] == [ref]]
            if not oids:
                return True
            if expected_oid is not None and oids[0] != expected_oid:
                return False
            self._git("push", f"--force-with-lease={ref}:{oids[0]}", "origin", f":{ref}")
        except PublishError as exc:
            raise PublishError(f"could not delete origin/{branch}: {exc}") from exc
        return True


@dataclass
class FakePublisher:
    """Test double: records calls; `fail` maps a method name to the PublishError message it raises.

    `states`/`heads`/`oids` are keyed by PR number (a PR's head defaults to the branch it was
    created from, its oid to the number as 40 hex digits); `remote_oids` maps a branch to the
    commit origin has for it (absent: whatever we expect; an expected "" never matches, as in
    `GhPublisher`); `existing` maps a branch to the PR `find_pr` returns.
    """

    unavailable: str | None = None
    states: dict[int, str] = field(default_factory=dict)
    heads: dict[int, str] = field(default_factory=dict)
    oids: dict[int, str] = field(default_factory=dict)
    remote_oids: dict[str, str] = field(default_factory=dict)
    existing: dict[str, PullRequest] = field(default_factory=dict)
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
        self.heads.setdefault(number, branch)
        return PullRequest(number, f"https://github.com/example/repo/pull/{number}")

    def find_pr(self, branch: str) -> PullRequest | None:
        self.calls.append(("find_pr", branch))
        self._maybe_fail("find_pr")
        return self.existing.get(branch)

    def pr_info(self, url: str) -> PrInfo:
        self.calls.append(("pr_info", url))
        self._maybe_fail("pr_info")
        match = _PR_NUMBER.search(url)
        assert match is not None, url
        number = int(match.group(1))
        return PrInfo(self.states.get(number, "open"), self.heads.get(number, ""),
                      self.oids.get(number, f"{number:040x}"))

    def delete_remote_branch(self, branch: str, expected_oid: str | None) -> bool:
        self.calls.append(("delete_remote_branch", branch, expected_oid))
        self._maybe_fail("delete_remote_branch")
        remote = self.remote_oids.get(branch)
        if expected_oid == "" or (remote is not None and expected_oid is not None and remote != expected_oid):
            return False
        self.deleted.append(branch)
        return True


def make_publisher(repo_root: Path) -> Publisher:
    return GhPublisher(repo_root)
