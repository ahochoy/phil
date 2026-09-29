from datetime import UTC, datetime, timedelta

import pytest

from phil.publish.publisher import FakePublisher
from phil.publish.service import PrChange, change_line, publish_run, sweep_prs
from phil.run.cleanup import CleanError
from phil.store.db import connect
from phil.store.runs import get_run, update_run
from tests.cli.test_diff_clean import finished_run


def published_run(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    fake = FakePublisher()
    record = publish_run(info, conn, get_run(conn, record.run_id), fake)
    # Pretend the PR was last checked long ago so the sweep's throttle doesn't skip it.
    record = update_run(conn, record.run_id, pr_checked_at="2000-01-01T00:00:00+00:00")
    fake.calls.clear()
    return info, conn, record, paths, fake


def learnings_text(paths) -> str:
    path = paths.project_dir / "learnings.md"
    return path.read_text() if path.exists() else ""


def test_a_merged_pr_appends_learnings_and_cleans_the_run(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"

    changes = sweep_prs(info, conn, fake)

    assert changes == [PrChange(record.run_id, 12, "merged")]
    stored = get_run(conn, record.run_id)
    assert stored.state == "cleaned"
    assert stored.pr_state == "merged"
    assert f"## {record.run_id} " in learnings_text(paths)
    assert fake.deleted == [record.branch]


def test_a_closed_pr_is_recorded_and_not_checked_again(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "closed"

    changes = sweep_prs(info, conn, fake)

    assert changes == [PrChange(record.run_id, 12, "closed")]
    stored = get_run(conn, record.run_id)
    assert stored.pr_state == "closed"
    assert stored.state == "completed"
    fake.calls.clear()
    assert sweep_prs(info, conn, fake, force=True) == []
    assert fake.calls == []


def test_an_open_pr_changes_nothing_but_the_checked_time(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)

    assert sweep_prs(info, conn, fake) == []

    stored = get_run(conn, record.run_id)
    assert stored.pr_state == "open"
    assert stored.state == "completed"
    assert stored.pr_checked_at != "2000-01-01T00:00:00+00:00"


def test_a_recently_checked_run_is_not_queried_unless_forced(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    now = datetime.now(UTC)
    update_run(conn, record.run_id, pr_checked_at=(now - timedelta(seconds=10)).isoformat())

    assert sweep_prs(info, conn, fake, now=now) == []
    assert fake.calls == []

    sweep_prs(info, conn, fake, now=now, force=True)
    assert fake.calls == [("pr_info", record.pr_url)]


def test_a_pr_check_error_skips_the_run_logs_it_and_keeps_the_checked_time(calc_repo, caplog):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.fail["pr_info"] = "gh pr view failed: offline"

    with caplog.at_level("INFO", logger="phil.publish.service"):
        assert sweep_prs(info, conn, fake) == []

    assert f"PR check for {record.run_id} failed: gh pr view failed: offline" in caplog.text

    assert get_run(conn, record.run_id).pr_checked_at == "2000-01-01T00:00:00+00:00"


def test_an_unavailable_publisher_touches_nothing(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.unavailable = "gh is not installed"

    assert sweep_prs(info, conn, fake, force=True) == []

    assert fake.calls == []
    assert get_run(conn, record.run_id).pr_checked_at == "2000-01-01T00:00:00+00:00"


def test_a_failed_cleanup_is_reported_and_retried_later(calc_repo, monkeypatch):
    import phil.publish.service as service

    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    real_clean_run = service.clean_run

    def boom(*args, **kwargs):
        raise CleanError("boom")

    monkeypatch.setattr("phil.publish.service.clean_run", boom)
    changes = sweep_prs(info, conn, fake)

    assert changes == [PrChange(record.run_id, 12, "cleanup_failed", "boom")]
    stored = get_run(conn, record.run_id)
    assert (stored.pr_state, stored.state) == ("merged", "completed")

    monkeypatch.setattr("phil.publish.service.clean_run", real_clean_run)
    fake.calls.clear()
    changes = sweep_prs(info, conn, fake)

    assert changes == [PrChange(record.run_id, 12, "merged")]
    assert get_run(conn, record.run_id).state == "cleaned"
    assert fake.calls[0] == ("pr_info", record.pr_url)  # re-read: the retry must know the PR's head
    assert learnings_text(paths).count(f"## {record.run_id} ") == 1


def test_a_remote_branch_failure_is_a_warning_after_cleanup(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    fake.fail["delete_remote_branch"] = f"could not delete origin/{record.branch}: denied"

    changes = sweep_prs(info, conn, fake)

    assert changes == [
        PrChange(record.run_id, 12, "warning", f"could not delete origin/{record.branch}: denied")
    ]
    assert get_run(conn, record.run_id).state == "cleaned"


def test_change_lines():
    assert change_line(PrChange("r-7f3a", 12, "merged")) == "r-7f3a merged (#12); cleaned up."
    assert change_line(PrChange("r-7f3a", 12, "closed")) == (
        "r-7f3a's PR #12 was closed without merging; `phil clean r-7f3a` removes it."
    )
    assert change_line(PrChange("r-7f3a", 12, "cleanup_failed", "boom")) == (
        "r-7f3a merged (#12), but cleanup failed: boom"
    )
    assert change_line(PrChange("r-7f3a", 12, "warning", "could not delete origin/x")) == (
        "r-7f3a merged (#12); cleaned up, but could not delete origin/x"
    )


def test_a_run_with_a_live_worker_is_skipped(calc_repo):
    import os

    from phil.store.db import utcnow

    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    update_run(conn, record.run_id, pid=os.getpid(), heartbeat_at=utcnow())

    assert sweep_prs(info, conn, fake, force=True) == []

    assert fake.calls == []
    assert get_run(conn, record.run_id).state == "completed"


def test_a_starting_worker_is_skipped(calc_repo):
    import os

    from phil.store.events import run_events

    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    run_events(paths, record.run_id).append("spawn", pid=os.getpid(), mode="continue")

    assert sweep_prs(info, conn, fake, force=True) == []

    assert fake.calls == []
    assert get_run(conn, record.run_id).state == "completed"


def test_a_run_that_is_not_settled_is_skipped(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    for state in ("pending", "running", "escalated"):
        conn.execute("UPDATE runs SET state = ? WHERE run_id = ?", (state, record.run_id))
        conn.commit()

        assert sweep_prs(info, conn, fake, force=True) == []

        assert fake.calls == []
        assert get_run(conn, record.run_id).pr_state == "open"


def test_an_unexpected_error_is_a_failed_cleanup_and_the_sweep_goes_on(calc_repo, monkeypatch):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise OSError("disk full")

    monkeypatch.setattr("phil.publish.service.append_learnings", boom)
    changes = sweep_prs(info, conn, fake)

    assert changes == [PrChange(record.run_id, 12, "cleanup_failed", "OSError: disk full")]
    stored = get_run(conn, record.run_id)
    assert (stored.pr_state, stored.state) == ("merged", "completed")


def test_an_unexpected_pr_check_error_is_logged_and_the_sweep_goes_on(calc_repo, caplog):
    info, conn, first, paths, fake = published_run(calc_repo)
    second = finished_run(calc_repo)[1]
    second = publish_run(info, conn, get_run(conn, second.run_id), fake)
    update_run(conn, second.run_id, pr_checked_at="2000-01-01T00:00:00+00:00")
    fake.states[13] = "merged"
    real_pr_info = fake.pr_info

    def pr_info(url):
        if url == first.pr_url:
            raise RuntimeError("gh output changed")
        return real_pr_info(url)

    fake.pr_info = pr_info
    with caplog.at_level("WARNING", logger="phil.publish.service"):
        changes = sweep_prs(info, conn, fake)

    assert changes == [PrChange(second.run_id, 13, "merged")]
    assert f"PR check for {first.run_id} failed" in caplog.text
    assert "RuntimeError: gh output changed" in caplog.text
    assert get_run(conn, first.run_id).pr_state == "open"


def test_one_failing_run_does_not_stop_the_others(calc_repo, monkeypatch):
    import phil.publish.service as service

    info, conn, first, paths, fake = published_run(calc_repo)
    second = finished_run(calc_repo)[1]
    second = publish_run(info, conn, get_run(conn, second.run_id), fake)
    update_run(conn, second.run_id, pr_checked_at="2000-01-01T00:00:00+00:00")
    fake.states[12] = "merged"
    fake.states[13] = "merged"
    real_clean_run = service.clean_run

    def clean_one(info_, conn_, record, **kwargs):
        if record.run_id == first.run_id:
            raise RuntimeError("unexpected")
        return real_clean_run(info_, conn_, record, **kwargs)

    monkeypatch.setattr("phil.publish.service.clean_run", clean_one)
    changes = sweep_prs(info, conn, fake)

    assert sorted(changes, key=lambda c: c.number) == [
        PrChange(first.run_id, 12, "cleanup_failed", "RuntimeError: unexpected"),
        PrChange(second.run_id, 13, "merged"),
    ]
    assert get_run(conn, second.run_id).state == "cleaned"


def test_the_remote_branch_is_deleted_only_at_the_prs_head_commit(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    fake.oids[12] = "a" * 40

    assert sweep_prs(info, conn, fake) == [PrChange(record.run_id, 12, "merged")]

    assert ("delete_remote_branch", record.branch, "a" * 40) in fake.calls
    assert fake.deleted == [record.branch]


def test_a_remote_branch_that_moved_is_left_alone_with_a_warning(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    fake.oids[12] = "a" * 40
    fake.remote_oids[record.branch] = "b" * 40  # another clone reused the branch name

    changes = sweep_prs(info, conn, fake)

    assert changes == [
        PrChange(record.run_id, 12, "warning", f"origin/{record.branch} now points elsewhere; left it alone")
    ]
    assert fake.deleted == []
    assert get_run(conn, record.run_id).state == "cleaned"


def test_a_pr_whose_head_is_another_branch_is_not_cleaned(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    fake.heads[12] = "someone/else"

    changes = sweep_prs(info, conn, fake)

    assert changes == [
        PrChange(record.run_id, 12, "cleanup_failed", f"PR #12's head is someone/else, not {record.branch}")
    ]
    stored = get_run(conn, record.run_id)
    assert (stored.pr_state, stored.state) == ("merged", "completed")
    assert fake.deleted == []
    assert learnings_text(paths) == ""


def test_a_run_cleaned_by_hand_is_marked_merged_without_learnings_or_cleanup(calc_repo, monkeypatch):
    from phil.run.cleanup import clean_run

    info, conn, record, paths, fake = published_run(calc_repo)
    clean_run(info, conn, record)
    fake.states[12] = "merged"
    monkeypatch.setattr("phil.publish.service.clean_run", lambda *a, **k: pytest.fail("cleaned twice"))

    changes = sweep_prs(info, conn, fake)

    assert changes == [PrChange(record.run_id, 12, "merged")]
    stored = get_run(conn, record.run_id)
    assert (stored.pr_state, stored.state) == ("merged", "cleaned")
    assert learnings_text(paths) == ""
    assert fake.deleted == []
    fake.calls.clear()
    assert sweep_prs(info, conn, fake, force=True) == []
    assert fake.calls == []


def test_a_pr_with_no_head_oid_leaves_the_remote_branch_alone(calc_repo):
    info, conn, record, paths, fake = published_run(calc_repo)
    fake.states[12] = "merged"
    fake.oids[12] = ""  # gh reported an empty headRefOid

    changes = sweep_prs(info, conn, fake)

    assert ("delete_remote_branch", record.branch, "") in fake.calls
    assert changes == [
        PrChange(record.run_id, 12, "warning", f"origin/{record.branch} now points elsewhere; left it alone")
    ]
    assert fake.deleted == []
    assert get_run(conn, record.run_id).state == "cleaned"
