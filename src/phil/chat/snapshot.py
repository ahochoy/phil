"""Read-only snapshot of a commit's tracked files, for agents that must not see the working tree."""

import io
import shutil
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
