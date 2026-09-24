import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepoError(Exception):
    pass


class NotAGitRepo(RepoError):
    pass


class NoCommits(RepoError):
    pass


@dataclass(frozen=True)
class RepoInfo:
    root: Path
    slug: str
    head_sha: str
    branch: str | None
    dirty_files: list[str]


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def resolve_repo(start: Path) -> RepoInfo:
    try:
        root = Path(_git(["rev-parse", "--show-toplevel"], start).strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError) as exc:
        raise NotAGitRepo(f"Not inside a git repository: {start}") from exc
    try:
        head_sha = _git(["rev-parse", "HEAD"], root).strip()
    except subprocess.CalledProcessError as exc:
        raise NoCommits(f"Repository has no commits yet: {root}") from exc
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root).strip()
    status = _git(["status", "--porcelain", "--untracked-files=all"], root)
    dirty = sorted(line[3:] for line in status.splitlines() if line)
    digest = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    return RepoInfo(
        root=root,
        slug=f"{root.name}-{digest}",
        head_sha=head_sha,
        branch=None if branch == "HEAD" else branch,
        dirty_files=dirty,
    )
