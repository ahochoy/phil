"""Publish a finished run: push its branch and open a pull request on GitHub.

Kept free of langgraph/langchain/deepagents at module import time (see
`implementer-common.md`).
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from phil.publish.learnings import append_learnings, learnings_entry
from phil.publish.pr_body import find_pr_template, pr_title, render_pr_body
from phil.publish.publisher import Publisher, PublishError
from phil.repo import RepoInfo
from phil.run.cleanup import CleanError, clean_run
from phil.run.launch import is_worker_alive, worker_starting
from phil.store.artifacts import ArtifactStore
from phil.store.db import utcnow
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import RunRecord, get_run, list_runs, update_run
from phil.store.telemetry import run_usage


logger = logging.getLogger(__name__)  # under `phil`: the chat routes it to phil.log; never printed

UNSETTLED = ("pending", "running", "escalated")  # a worker may own these runs; the sweep leaves them


class PublishRefused(Exception):
    """Publishing was refused before anything happened; the message is one printable line."""


def publish_run(
    info: RepoInfo,
    conn: sqlite3.Connection,
    record: RunRecord,
    publisher: Publisher,
    *,
    base: str | None = None,
) -> RunRecord:
    """Push `record`'s branch and open its pull request, then record the PR on the run.

    Raises `PublishRefused` (nothing done) for any of: the run isn't `completed`; it already
    has a PR; no base branch is known (pass `base`); or `publisher.available()` names a
    reason. A `PublishError` from `publisher.push`/`create_pr` propagates unchanged and the
    run row is left untouched (re-running is safe: `git push` of the same ref is idempotent).
    """
    if record.state != "completed":
        raise PublishRefused(f"{record.run_id} is {record.state}; only a completed run can be published")
    if record.pr_url is not None:
        raise PublishRefused(f"{record.run_id} already has PR #{record.pr_number}: {record.pr_url}")
    base_branch = base or record.base_branch
    if base_branch is None:
        raise PublishRefused(f"{record.run_id} started on a detached HEAD; pass --base <branch>")
    reason = publisher.available()
    if reason is not None:
        raise PublishRefused(reason)

    publisher.push(record.branch)

    run_dir = ProjectPaths(info.slug).run_dir(record.run_id)
    plan = ArtifactStore(run_dir).read_plan()
    totals = run_usage(conn, record.run_id)
    template = find_pr_template(info.root)
    body = render_pr_body(run_id=record.run_id, run_dir=run_dir, totals=totals, template=template)
    pull_request = publisher.create_pr(branch=record.branch, base=base_branch, title=pr_title(plan), body=body)

    return update_run(
        conn,
        record.run_id,
        pr_url=pull_request.url,
        pr_number=pull_request.number,
        pr_state="open",
        pr_checked_at=utcnow(),
        base_branch=base_branch,
    )


@dataclass(frozen=True)
class PrChange:
    """Something a PR sweep did to a run, for the caller to report.

    `kind` is `merged` (cleaned up), `closed`, `cleanup_failed` (merged, but `clean_run`
    raised; `detail` is the error line) or `warning` (cleaned up, but deleting the remote
    branch failed; `detail` is the line).
    """

    run_id: str
    number: int
    kind: str
    detail: str = ""


def _due(record: RunRecord, now: datetime, min_interval_s: float) -> bool:
    if record.pr_checked_at is None:
        return True
    checked = datetime.fromisoformat(record.pr_checked_at)
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return (now - checked).total_seconds() >= min_interval_s


def _clean_merged(info: RepoInfo, conn: sqlite3.Connection, record: RunRecord, publisher: Publisher) -> PrChange:
    number = record.pr_number
    assert number is not None
    paths = ProjectPaths(info.slug)
    today = datetime.now().astimezone().date().isoformat()
    entry = learnings_entry(record=record, run_dir=paths.run_dir(record.run_id), today=today)
    append_learnings(paths, entry, record.run_id)
    try:
        clean_run(info, conn, record, publisher=publisher)
    except CleanError as exc:
        current = get_run(conn, record.run_id)
        kind = "warning" if current is not None and current.state == "cleaned" else "cleanup_failed"
        return PrChange(record.run_id, number, kind, str(exc))
    return PrChange(record.run_id, number, "merged")


def sweep_prs(
    info: RepoInfo,
    conn: sqlite3.Connection,
    publisher: Publisher,
    *,
    now: datetime | None = None,
    min_interval_s: float = 300.0,
    force: bool = False,
) -> list[PrChange]:
    """Check runs with an open PR; clean up merged ones and record closed ones.

    Open PRs are checked at most every `min_interval_s` seconds (unless `force`); merged runs
    whose cleanup failed earlier are always retried. Runs that are pending/running/escalated or
    have a live or starting worker are skipped, and an error from `pr_state` skips that run this
    time. Any error while cleaning one merged run becomes its `cleanup_failed` change and the
    sweep goes on. Never prints.
    """
    now = now or datetime.now(UTC)
    candidates = [
        record
        for record in list_runs(conn)
        if record.pr_number is not None
        and (
            (record.pr_state == "open" and (force or _due(record, now, min_interval_s)))
            or (record.pr_state == "merged" and record.state != "cleaned")
        )
    ]
    if not candidates or publisher.available() is not None:
        return []

    paths = ProjectPaths(info.slug)
    changes: list[PrChange] = []
    for record in candidates:
        if record.state in UNSETTLED or is_worker_alive(record) or worker_starting(run_events(paths, record.run_id)):
            continue
        number = record.pr_number
        assert number is not None
        if record.pr_state == "open":
            try:
                state = publisher.pr_state(number)
                record = update_run(conn, record.run_id, pr_checked_at=utcnow())
                if state == "closed":
                    update_run(conn, record.run_id, pr_state="closed")
                    changes.append(PrChange(record.run_id, number, "closed"))
                    continue
                if state != "merged":
                    continue
                record = update_run(conn, record.run_id, pr_state="merged")
            except PublishError:  # gh failed: try again next sweep
                continue
            except Exception:  # a surprise: leave a trace in the log (the chat's phil.log) and move on
                logger.warning("PR check for %s failed", record.run_id, exc_info=True)
                continue
        try:
            changes.append(_clean_merged(info, conn, record, publisher))
        except Exception as exc:  # one run's failure must not stop the sweep (the chat runs it unattended)
            changes.append(PrChange(record.run_id, number, "cleanup_failed", f"{type(exc).__name__}: {exc}"))
    return changes


def change_line(change: PrChange) -> str:
    """One plain-text line describing `change` (callers escape it for rich)."""
    run_id, number = change.run_id, change.number
    if change.kind == "merged":
        return f"{run_id} merged (#{number}); cleaned up."
    if change.kind == "closed":
        return f"{run_id}'s PR #{number} was closed without merging; `phil clean {run_id}` removes it."
    if change.kind == "cleanup_failed":
        return f"{run_id} merged (#{number}), but cleanup failed: {change.detail}"
    return f"{run_id} merged (#{number}); cleaned up, but {change.detail}"
