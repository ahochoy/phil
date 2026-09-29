from pathlib import Path

from phil.store.db import connect
from phil.store.runs import create_run, get_run, update_run


def test_new_run_records_its_base_branch(tmp_path: Path):
    conn = connect(tmp_path / "phil.db")
    run = create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=tmp_path / "wt",
                     tasks_total=1, base_branch="main")
    assert run.base_branch == "main"
    assert (run.pr_url, run.pr_number, run.pr_state, run.pr_checked_at) == (None, None, None, None)


def test_pr_fields_are_updatable(tmp_path: Path):
    conn = connect(tmp_path / "phil.db")
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=tmp_path / "wt", tasks_total=1)
    update_run(conn, "r-0001", pr_url="https://github.com/o/r/pull/12", pr_number=12, pr_state="open",
               pr_checked_at="2026-09-29T00:00:00+00:00")
    run = get_run(conn, "r-0001")
    assert (run.pr_number, run.pr_state) == (12, "open")
    assert run.base_branch is None
