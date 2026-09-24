from dataclasses import dataclass
from pathlib import Path

from phil.git import GitError, branch_for, git


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_sha: str


class WorktreeManager:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def create(self, *, run_id: str, base_sha: str, path: Path) -> Worktree:
        branch = branch_for(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(self.repo_root, "worktree", "add", "-b", branch, str(path), base_sha)
        return Worktree(path=path, branch=branch, base_sha=base_sha)

    def remove(self, worktree: Worktree, *, delete_branch: bool = True) -> None:
        git(self.repo_root, "worktree", "remove", "--force", str(worktree.path))
        if delete_branch:
            git(self.repo_root, "branch", "-D", worktree.branch)

    def changed_files(self, path: Path, since: str) -> list[str]:
        tracked = git(path, "diff", "--name-only", since).splitlines()
        untracked = git(path, "ls-files", "--others", "--exclude-standard").splitlines()
        return sorted(set(tracked) | set(untracked))

    def commit_all(self, path: Path, message: str) -> str:
        git(path, "add", "-A")
        # Unattended workers must never block on a hook or a signing prompt (spec §7).
        git(path, "-c", "commit.gpgsign=false", "commit", "--no-verify", "-m", message)
        return self.head(path)

    def diff(self, path: Path, base: str) -> str:
        return git(path, "diff", base, "HEAD")

    def head(self, path: Path) -> str:
        return git(path, "rev-parse", "HEAD").strip()

    def reset_to(self, path: Path, sha: str) -> None:
        git(path, "reset", "--hard", sha)
        git(path, "clean", "-fd")

    def restore(self, path: Path, paths: list[str]) -> None:
        for relative in paths:
            try:
                git(path, "cat-file", "-e", f"HEAD:{relative}")
            except GitError:
                target = path / relative
                if target.is_file():
                    target.unlink()
                continue
            git(path, "checkout", "HEAD", "--", relative)

    def snapshot(self, path: Path) -> str:
        """Record the whole working tree (tracked and untracked) as a git tree object; returns its sha."""
        git(path, "add", "-A")
        tree = git(path, "write-tree").strip()
        git(path, "reset", "-q")
        return tree

    def restore_snapshot(self, path: Path, tree: str) -> None:
        git(path, "read-tree", "-u", "--reset", tree)
        git(path, "clean", "-fd")
        git(path, "reset", "-q")
