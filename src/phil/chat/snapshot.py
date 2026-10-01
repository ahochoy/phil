"""Read-only snapshots for agents that must not read the live repo root: a commit's tracked files
(`export_tree`), or the working tree's non-ignored files (`export_worktree`)."""

import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

from phil.git import GitError, GitNotFound


def _marker(dest: Path, sha: str) -> Path:
    return dest / f".phil-snapshot-{sha}"


def _safe_members(member: tarfile.TarInfo, dest_path: str) -> tarfile.TarInfo | None:
    # Skip entries the "data" filter rejects (absolute links, links leaving the snapshot) instead of
    # aborting the export, so a repo with such a committed symlink can still be planned against.
    try:
        return tarfile.data_filter(member, dest_path)
    except tarfile.FilterError:
        return None


def export_tree(repo_root: Path, sha: str, dest: Path) -> Path:
    """Export the tracked files of commit `sha` into `dest` (untracked and ignored files never appear).

    A complete earlier export of the same sha (marked by `.phil-snapshot-<sha>`) is reused.
    """
    if _marker(dest, sha).exists():
        return dest
    args = ["archive", "--format=tar", sha]
    try:
        proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True)
    except FileNotFoundError as exc:
        raise GitNotFound(args, 127, str(exc)) from exc
    if proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr.decode(errors="replace"))
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
        archive.extractall(dest, filter=_safe_members)
    _marker(dest, sha).write_text("")
    return dest


def _worktree_paths(root: Path) -> list[str]:
    args = ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True)
    except FileNotFoundError as exc:
        raise GitNotFound(args, 127, str(exc)) from exc
    if proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr.decode(errors="replace"))
    return sorted({os.fsdecode(raw) for raw in proc.stdout.split(b"\0") if raw})


def _copy_link(source: Path, target: Path, real_root: Path) -> None:
    """Recreate a symlink as a relative link inside the snapshot, if it resolves inside the repo."""
    resolved = source.resolve()
    if not resolved.is_relative_to(real_root) or resolved == real_root:
        return
    # Point at the same repo-relative path in the snapshot: an ignored target (`.env`) isn't there,
    # so such a link dangles instead of reaching the live file.
    real_parent = source.parent.resolve()
    target.symlink_to(os.path.relpath(resolved, real_parent))


def export_worktree(root: Path, dest: Path) -> Path:
    """Copy the working tree's tracked and untracked, non-ignored files into a fresh `dest`.

    Each file has its current working-tree contents, so uncommitted edits are visible, while ignored
    files (`.env`, `.venv`, local key files) never exist in the snapshot. Paths deleted from the
    working tree, non-regular files and symlinks resolving outside the repo are skipped.
    """
    paths = _worktree_paths(root)
    if dest.exists() or dest.is_symlink():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    real_root = root.resolve()
    for rel in paths:
        source = root / rel
        try:
            mode = source.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            continue  # deleted in the working tree but still tracked
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            continue
        if not source.parent.resolve().is_relative_to(real_root):
            continue  # a directory on the way was replaced by a link leaving the repo
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(mode):
            _copy_link(source, target, real_root)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
    return dest
