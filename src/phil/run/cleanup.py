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
from phil.workspace.worktree import Worktree, WorktreeManager

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
) -> None:
    """Remove a finished run's worktree, branch, checkpoints, and scratch files.

    Raises `CleanError` (never prints or exits). When `publisher` is given, the remote
    branch is deleted last: a `PublishError` there is re-raised as `CleanError` only after
    the run is already marked `cleaned`, so callers never repeat the local cleanup.
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

    try:
        git(info.root, "show-ref", "--verify", "--quiet", f"refs/heads/{record.branch}")
        branch_exists = True
    except GitError:
        branch_exists = False
    if branch_exists:
        try:
            git(info.root, "branch", "-D", record.branch)
        except GitError as exc:
            raise CleanError(str(exc)) from exc

    manager.delete_refs(f"refs/phil/{record.run_id}/")
    saver = open_checkpointer(paths.db_path)
    try:
        saver.delete_thread(record.run_id)
    finally:
        saver.conn.close()

    run_dir = paths.run_dir(record.run_id)
    if run_dir.exists():
        if purge:
            shutil.rmtree(run_dir)
        else:
            for child in run_dir.iterdir():
                if child.name in _KEPT_FILES:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

    update_run(conn, record.run_id, state="cleaned", needs_attention=None)

    if publisher is not None:
        try:
            publisher.delete_remote_branch(record.branch)
        except PublishError as exc:
            raise CleanError(str(exc)) from exc
