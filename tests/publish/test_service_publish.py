import pytest

from phil.publish.publisher import FakePublisher, PublishError
from phil.publish.service import PublishRefused, publish_run
from phil.store.db import connect
from phil.store.runs import get_run, update_run
from tests.cli.test_diff_clean import failed_run, finished_run


def test_publish_run_pushes_and_opens_the_pull_request(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher()

    updated = publish_run(info, conn, record, fake)

    assert updated.pr_number == 12
    assert updated.pr_state == "open"
    assert fake.pushed == [record.branch]
    create_call = next(call for call in fake.calls if call[0] == "create_pr")
    _, branch, base, title, body = create_call
    assert branch == record.branch
    assert base == "main"
    assert title.startswith("CALC:")
    assert isinstance(body, str) and body
    stored = get_run(conn, record.run_id)
    assert (stored.pr_number, stored.pr_url, stored.pr_state) == (12, updated.pr_url, "open")
    assert stored.base_branch == "main"


def test_publish_run_refuses_a_run_that_is_not_completed(calc_repo):
    info, record, paths = failed_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher()

    with pytest.raises(PublishRefused, match="failed; only a completed run can be published"):
        publish_run(info, conn, record, fake)
    assert fake.calls == []


def test_publish_run_refuses_a_run_that_already_has_a_pr(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = update_run(conn, record.run_id, pr_url="https://github.com/o/r/pull/9", pr_number=9, pr_state="open")
    assert record.state == "completed"
    fake = FakePublisher()

    with pytest.raises(PublishRefused, match=r"already has PR #9: https://github.com/o/r/pull/9"):
        publish_run(info, conn, record, fake)
    assert fake.calls == []


def test_publish_run_refuses_a_detached_run_without_a_base(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    conn.execute("UPDATE runs SET base_branch = NULL WHERE run_id = ?", (record.run_id,))
    record = get_run(conn, record.run_id)
    fake = FakePublisher()

    with pytest.raises(PublishRefused, match="detached HEAD; pass --base <branch>"):
        publish_run(info, conn, record, fake)
    assert fake.calls == []


def test_publish_run_base_option_overrides_a_missing_base_branch(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    conn.execute("UPDATE runs SET base_branch = NULL WHERE run_id = ?", (record.run_id,))
    record = get_run(conn, record.run_id)
    fake = FakePublisher()

    updated = publish_run(info, conn, record, fake, base="develop")

    assert updated.base_branch == "develop"
    create_call = next(call for call in fake.calls if call[0] == "create_pr")
    assert create_call[2] == "develop"


def test_publish_run_refuses_when_the_publisher_is_unavailable(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher(unavailable="gh is not logged in; run `gh auth login`")

    with pytest.raises(PublishRefused, match="gh auth login"):
        publish_run(info, conn, record, fake)
    assert fake.calls == []


def test_publish_run_push_failure_is_a_publish_error_and_leaves_pr_url_none(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher(fail={"push": "rejected"})

    with pytest.raises(PublishError, match="rejected"):
        publish_run(info, conn, record, fake)
    assert get_run(conn, record.run_id).pr_url is None


def test_publish_run_create_pr_failure_after_a_successful_push_leaves_pr_url_none(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher(fail={"create_pr": "a pull request already exists"})

    with pytest.raises(PublishError, match="already exists"):
        publish_run(info, conn, record, fake)
    assert fake.pushed == [record.branch]
    assert get_run(conn, record.run_id).pr_url is None
