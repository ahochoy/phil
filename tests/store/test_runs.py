import re
from pathlib import Path

import pytest

from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, get_run, list_runs, new_run_id, update_run


@pytest.fixture
def conn(phil_home):
    paths = ProjectPaths("demo-12345678")
    return connect(paths.db_path)


def test_project_paths_live_under_phil_home(phil_home):
    paths = ProjectPaths("demo-12345678")
    assert paths.project_dir == phil_home / "projects" / "demo-12345678"
    assert paths.db_path == paths.project_dir / "phil.db"
    assert paths.run_dir("r-0001") == paths.project_dir / "runs" / "r-0001"
    assert paths.worktree_dir("r-0001") == paths.project_dir / "worktrees" / "r-0001"


def test_connect_creates_db_file(phil_home):
    paths = ProjectPaths("demo-12345678")
    connect(paths.db_path)
    assert paths.db_path.exists()


def test_new_run_id_format(conn):
    assert re.fullmatch(r"r-[0-9a-f]{4}", new_run_id(conn))


def test_create_and_get_run(conn):
    run = create_run(
        conn, run_id="r-0001", keyword="MAPS", base_sha="abc123", worktree=Path("/tmp/wt"), tasks_total=5
    )
    assert run.branch == "phil/r-0001"
    assert run.state == "pending"
    assert run.tasks_done == 0
    assert get_run(conn, "r-0001") == run


def test_get_missing_run_returns_none(conn):
    assert get_run(conn, "r-ffff") is None


def test_list_runs_newest_first(conn):
    for run_id in ("r-0001", "r-0002"):
        create_run(conn, run_id=run_id, keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    assert [run.run_id for run in list_runs(conn)] == ["r-0002", "r-0001"]


def test_update_run_changes_fields(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=3)
    updated = update_run(conn, "r-0001", state="running", current_node="implement", tasks_done=1)
    assert (updated.state, updated.current_node, updated.tasks_done) == ("running", "implement", 1)


def test_update_run_rejects_unknown_fields(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=3)
    with pytest.raises(ValueError):
        update_run(conn, "r-0001", keyword="AUTH")


def test_update_missing_run_raises(conn):
    with pytest.raises(KeyError):
        update_run(conn, "r-ffff", state="running")
