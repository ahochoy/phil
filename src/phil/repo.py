import hashlib
from dataclasses import dataclass
from pathlib import Path

from phil.git import GitError, GitNotFound, git


class RepoError(Exception):
    pass


class NotAGitRepo(RepoError):
    pass


class NoCommits(RepoError):
    pass


class GitMissing(RepoError):
    pass


@dataclass(frozen=True)
class RepoInfo:
    root: Path
    slug: str
    head_sha: str
    branch: str | None
    dirty_files: list[str]


def resolve_repo(start: Path) -> RepoInfo:
    try:
        root = Path(git(start, "rev-parse", "--show-toplevel").strip()).resolve()
    except GitNotFound as exc:
        raise GitMissing("git executable not found") from exc
    except (GitError, NotADirectoryError, FileNotFoundError) as exc:
        raise NotAGitRepo(f"Not inside a git repository: {start}") from exc
    try:
        head_sha = git(root, "rev-parse", "HEAD").strip()
    except GitError as exc:
        raise NoCommits(f"Repository has no commits yet: {root}") from exc
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    status = git(root, "status", "--porcelain", "--untracked-files=all")
    dirty = sorted(line[3:] for line in status.splitlines() if line)
    digest = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    return RepoInfo(
        root=root,
        slug=f"{root.name}-{digest}",
        head_sha=head_sha,
        branch=None if branch == "HEAD" else branch,
        dirty_files=dirty,
    )
