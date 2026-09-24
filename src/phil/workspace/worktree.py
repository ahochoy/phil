import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_sha: str


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


class WorktreeManager:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def create(self, *, run_id: str, base_sha: str, path: Path) -> Worktree:
        branch = f"phil/{run_id}"
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo_root, "worktree", "add", "-b", branch, str(path), base_sha)
        return Worktree(path=path, branch=branch, base_sha=base_sha)

    def remove(self, worktree: Worktree, *, delete_branch: bool = True) -> None:
        _git(self.repo_root, "worktree", "remove", "--force", str(worktree.path))
        if delete_branch:
            _git(self.repo_root, "branch", "-D", worktree.branch)

    def changed_files(self, path: Path, since: str) -> list[str]:
        tracked = _git(path, "diff", "--name-only", since).splitlines()
        untracked = _git(path, "ls-files", "--others", "--exclude-standard").splitlines()
        return sorted(set(tracked) | set(untracked))

    def commit_all(self, path: Path, message: str) -> str:
        _git(path, "add", "-A")
        _git(path, "commit", "-m", message)
        return self.head(path)

    def diff(self, path: Path, base: str) -> str:
        return _git(path, "diff", base, "HEAD")

    def head(self, path: Path) -> str:
        return _git(path, "rev-parse", "HEAD").strip()
