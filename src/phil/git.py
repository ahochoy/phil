import re
import subprocess
from pathlib import Path

RUN_ID_PATTERN = r"^r-[0-9a-f]{4}$"


class GitError(Exception):
    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        super().__init__(command, returncode, stderr)
        self.command = list(command)
        self.returncode = returncode
        self.stderr = stderr

    def __str__(self) -> str:
        return f"git {' '.join(self.command)} failed (exit {self.returncode}): {self.stderr}"


class GitNotFound(GitError):
    pass


def git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        if exc.filename == "git":
            raise GitNotFound(list(args), 127, str(exc)) from exc
        raise
    if proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc.stdout


def current_branch(root: Path) -> str:
    """The branch checked out in `root`, the short SHA on a detached HEAD, or "?" on any error. Never raises."""
    try:
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch == "HEAD":
            return git(root, "rev-parse", "--short", "HEAD").strip()
        return branch or "?"
    except Exception:
        return "?"


def commits_ahead(repo: Path, base: str, branch: str) -> int:
    """How many commits `branch` has beyond `base` in `repo`; 0 when the branch doesn't exist.
    Raises `GitError` for any other git failure, so the real error surfaces."""
    try:
        git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    except GitError as exc:
        if exc.returncode == 1:  # show-ref's "no such ref"; anything else is a real failure
            return 0
        raise
    return int(git(repo, "rev-list", "--count", f"{base}..refs/heads/{branch}").strip())


def branch_for(run_id: str) -> str:
    if not re.fullmatch(RUN_ID_PATTERN, run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    return f"phil/{run_id}"
