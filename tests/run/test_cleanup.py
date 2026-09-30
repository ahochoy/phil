from pathlib import Path

import pytest

from phil.publish.publisher import FakePublisher
from phil.repo import resolve_repo
from phil.run.cleanup import CleanError, clean_run
from phil.run.launch import prepare_run
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run, update_run
from tests.cli.test_diff_clean import finished_run
from tests.run.conftest import calc_plan


def test_clean_run_keeps_summary_and_open_issues(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    run_dir = paths.run_dir(record.run_id)
    (run_dir / "open_issues.json").write_text("[]")

    clean_run(info, conn, record)

    assert sorted(p.name for p in run_dir.iterdir()) == ["open_issues.json", "summary.md"]
    assert get_run(conn, record.run_id).state == "cleaned"


def test_clean_run_purge_removes_everything(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    run_dir = paths.run_dir(record.run_id)

    clean_run(info, conn, record, purge=True)

    assert not run_dir.exists()
    assert get_run(conn, record.run_id).state == "cleaned"


def test_clean_run_refuses_a_running_run(calc_repo):
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    record = prepare_run(info, calc_plan(), info.head_sha)
    update_run(conn, record.run_id, state="running")
    record = get_run(conn, record.run_id)

    with pytest.raises(CleanError):
        clean_run(info, conn, record)


def test_clean_run_deletes_the_remote_branch(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher()

    clean_run(info, conn, record, publisher=fake, expected_remote_oid="a" * 40)

    assert fake.calls == [("delete_remote_branch", record.branch, "a" * 40)]
    assert fake.deleted == [record.branch]
    assert get_run(conn, record.run_id).state == "cleaned"


def test_a_remote_branch_that_moved_is_reported_after_cleaning(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher(remote_oids={record.branch: "b" * 40})

    with pytest.raises(CleanError, match="now points elsewhere; left it alone"):
        clean_run(info, conn, record, publisher=fake, expected_remote_oid="a" * 40)

    assert fake.deleted == []
    assert get_run(conn, record.run_id).state == "cleaned"


def test_remote_deletion_failure_is_reported_after_cleaning(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    fake = FakePublisher(fail={"delete_remote_branch": "denied"})

    with pytest.raises(CleanError, match="denied"):
        clean_run(info, conn, record, publisher=fake)

    assert get_run(conn, record.run_id).state == "cleaned"


def test_a_second_cleanup_with_a_stale_record_does_not_raise(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    stale = get_run(conn, record.run_id)

    clean_run(info, conn, stale)
    clean_run(info, conn, stale)  # e.g. the chat's monitor and `phil runs` racing

    assert get_run(conn, record.run_id).state == "cleaned"


def test_files_vanishing_during_cleanup_are_ignored(calc_repo, monkeypatch):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    run_dir = paths.run_dir(record.run_id)
    (run_dir / "scratch").mkdir(exist_ok=True)
    (run_dir / "scratch.txt").write_text("x")
    real_unlink = type(run_dir).unlink

    def gone_rmtree(path, *args, **kwargs):
        raise FileNotFoundError(path)

    def gone_unlink(self, *args, **kwargs):
        real_unlink(self)
        raise FileNotFoundError(self)  # as if another cleanup removed it first

    monkeypatch.setattr("phil.run.cleanup.shutil.rmtree", gone_rmtree)
    monkeypatch.setattr(type(run_dir), "unlink", gone_unlink)

    clean_run(info, conn, record)

    assert get_run(conn, record.run_id).state == "cleaned"


def test_a_purge_of_a_run_dir_that_vanished_is_ignored(calc_repo, monkeypatch):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)

    def gone_rmtree(path, *args, **kwargs):
        raise FileNotFoundError(path)

    monkeypatch.setattr("phil.run.cleanup.shutil.rmtree", gone_rmtree)

    clean_run(info, conn, record, purge=True)

    assert get_run(conn, record.run_id).state == "cleaned"


def test_clean_run_removes_a_leftover_rebaseline_worktree(calc_repo):
    from phil.workspace.worktree import rebaseline_path
    from tests.helpers import run_git

    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    record = get_run(conn, record.run_id)
    leftover = rebaseline_path(Path(record.worktree))
    run_git(calc_repo, "worktree", "add", "--detach", str(leftover), record.base_sha)

    clean_run(info, conn, record)

    assert not leftover.exists()
    assert str(leftover) not in run_git(calc_repo, "worktree", "list", "--porcelain")
