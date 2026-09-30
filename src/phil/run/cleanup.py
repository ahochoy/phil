"""Shared run cleanup: the mechanics behind `phil clean`, reused by merge cleanup.

Kept free of langgraph/langchain/deepagents at module import time (see
`implementer-common.md`); `open_checkpointer` is imported inside `clean_run` instead.
"""

import shutil
import sqlite3
from pathlib import Path

from phil.git import GitError, git
from phil.publish.publisher import Publisher, PublishError
from phil.repo import RepoInfo
from phil.store.paths import ProjectPaths
from phil.store.runs import RunRecord, update_run
from phil.workspace.worktree import Worktree, WorktreeManager, rebaseline_path

_KEPT_FILES = {"summary.md", "open_issues.json"}


class CleanError(Exception):
    """A cleanup step failed; the message is one printable line."""


def clean_run(
    info: RepoInfo,
    conn: sqlite3.Connection,
    record: RunRecord,
    *,
    purge: bool = False,
    publisher: Publisher | None = None,
    expected_remote_oid: str | None = None,
) -> None:
    """Remove a finished run's worktree, branch, checkpoints, and scratch files.

    Raises `CleanError` (never prints or exits). When `publisher` is given, the remote
    branch is deleted last, and only if it still points at `expected_remote_oid` (when given):
    a `PublishError` there, or a remote branch that now points elsewhere (left alone), is
    raised as `CleanError` only after the run is already marked `cleaned`, so callers never
    repeat the local cleanup. Safe to run again, or concurrently with another cleanup of the
    same run: files that vanish underneath it are ignored.
    """
    from phil.run.checkpoint import open_checkpointer

    if record.state in ("pending", "running", "escalated"):
        raise CleanError(
            f"{record.run_id} is {record.state}; finish or stop it first "
            f"(`phil stop {record.run_id}` or `phil resume {record.run_id} --action abort`)"
        )

    paths = ProjectPaths(info.slug)
    manager = WorktreeManager(info.root)
    worktree = Path(record.worktree)
    try:
        if worktree.exists():
            manager.remove(Worktree(worktree, record.branch, record.base_sha), delete_branch=False)
        else:
            git(info.root, "worktree", "prune")
    except GitError as exc:
        raise CleanError(str(exc)) from exc
    _remove_rebaseline_worktree(info.root, worktree)

    branch_exists = _branch_exists(info.root, record.branch)
    if branch_exists:
        try:
            git(info.root, "branch", "-D", record.branch)
        except GitError as exc:
            if _branch_exists(info.root, record.branch):  # not just removed by a concurrent cleanup
                raise CleanError(str(exc)) from exc

    manager.delete_refs(f"refs/phil/{record.run_id}/")
    saver = open_checkpointer(paths.db_path)
    try:
        saver.delete_thread(record.run_id)
    finally:
        saver.conn.close()

    _trim_run_dir(paths.run_dir(record.run_id), purge=purge)

    update_run(conn, record.run_id, state="cleaned", needs_attention=None)

    if publisher is not None:
        try:
            gone = publisher.delete_remote_branch(record.branch, expected_remote_oid)
        except PublishError as exc:
            raise CleanError(str(exc)) from exc
        if not gone:
            raise CleanError(f"origin/{record.branch} now points elsewhere; left it alone")


def _remove_rebaseline_worktree(root: Path, worktree: Path) -> None:
    """Best effort: a worker that died mid-rebaseline can leave its temporary worktree behind."""
    leftover = rebaseline_path(worktree)
    if not leftover.exists():
        return
    try:
        git(root, "worktree", "remove", "--force", str(leftover))
    except GitError:
        pass
    shutil.rmtree(leftover, ignore_errors=True)
    try:
        git(root, "worktree", "prune")
    except GitError:
        pass


def _branch_exists(root: Path, branch: str) -> bool:
    try:
        git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    except GitError:
        return False
    return True


def _trim_run_dir(run_dir: Path, *, purge: bool) -> None:
    """Delete the run dir (`purge`) or everything in it but the kept files; anything already
    gone (another cleanup got there first) is fine."""
    try:
        if purge:
            shutil.rmtree(run_dir)
            return
        children = list(run_dir.iterdir())
    except FileNotFoundError:
        return
    for child in children:
        if child.name in _KEPT_FILES:
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            continue
