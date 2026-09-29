"""Publish a finished run: push its branch and open a pull request on GitHub.

Kept free of langgraph/langchain/deepagents at module import time (see
`implementer-common.md`).
"""

import sqlite3

from phil.publish.pr_body import find_pr_template, pr_title, render_pr_body
from phil.publish.publisher import Publisher
from phil.repo import RepoInfo
from phil.store.artifacts import ArtifactStore
from phil.store.db import utcnow
from phil.store.paths import ProjectPaths
from phil.store.runs import RunRecord, update_run
from phil.store.telemetry import run_usage


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
