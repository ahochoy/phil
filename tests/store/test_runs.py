import re
from pathlib import Path

import pytest

from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import (
    InvalidTransition,
    claim_run,
    create_run,
    get_run,
    list_runs,
    new_run_id,
    release_run,
    update_run,
)


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


def test_update_run_with_no_fields_raises(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=3)
    with pytest.raises(ValueError):
        update_run(conn, "r-0001")


def test_create_run_rejects_invalid_run_id(conn):
    with pytest.raises(ValueError):
        create_run(
            conn, run_id="bad", keyword="MAPS", base_sha="abc123", worktree=Path("/tmp/wt"), tasks_total=5
        )


def test_valid_transitions(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    for state in ["running", "escalated", "running", "stopped", "running", "completed", "cleaned"]:
        assert update_run(conn, "r-0001", state=state).state == state


def test_same_state_is_allowed(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    update_run(conn, "r-0001", state="running")
    update_run(conn, "r-0001", state="completed")
    assert update_run(conn, "r-0001", state="completed").state == "completed"


def test_invalid_transitions_are_rejected(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    with pytest.raises(InvalidTransition, match="pending to completed"):
        update_run(conn, "r-0001", state="completed")
    update_run(conn, "r-0001", state="running")
    update_run(conn, "r-0001", state="completed")
    with pytest.raises(InvalidTransition):
        update_run(conn, "r-0001", state="running")


def make(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)


def test_claim_run_takes_a_free_row_to_running(conn):
    make(conn)
    update_run(conn, "r-0001", needs_attention="old note")
    assert claim_run(conn, "r-0001", 111, "2026-01-01T00:00:00+00:00") is True
    run = get_run(conn, "r-0001")
    assert (run.state, run.pid, run.heartbeat_at, run.needs_attention) == (
        "running", 111, "2026-01-01T00:00:00+00:00", None
    )
    assert claim_run(conn, "r-0001", 111, "2026-01-01T00:00:01+00:00") is True


def test_claim_run_fails_when_another_pid_holds_the_row(conn):
    make(conn)
    update_run(conn, "r-0001", state="running", pid=222)
    assert claim_run(conn, "r-0001", 111, "2026-01-01T00:00:00+00:00") is False
    assert get_run(conn, "r-0001").pid == 222


def test_claim_run_can_take_over_a_known_stale_pid(conn):
    make(conn)
    update_run(conn, "r-0001", state="running", pid=222)
    assert claim_run(conn, "r-0001", 111, "2026-01-01T00:00:00+00:00", stale_pid=333) is False
    assert claim_run(conn, "r-0001", 111, "2026-01-01T00:00:00+00:00", stale_pid=222) is True
    assert get_run(conn, "r-0001").pid == 111


@pytest.mark.parametrize("state", ["completed", "aborted", "cleaned"])
def test_claim_run_respects_the_transition_table(conn, state):
    make(conn)
    update_run(conn, "r-0001", state="running")
    update_run(conn, "r-0001", state="completed" if state != "aborted" else "aborted")
    if state == "cleaned":
        update_run(conn, "r-0001", state="cleaned")
    assert claim_run(conn, "r-0001", 111, "2026-01-01T00:00:00+00:00") is False
    assert get_run(conn, "r-0001").state == state


@pytest.mark.parametrize("state", ["escalated", "stopped", "failed"])
def test_claim_run_from_resumable_states(conn, state):
    make(conn)
    update_run(conn, "r-0001", state="running")
    update_run(conn, "r-0001", state=state)
    assert claim_run(conn, "r-0001", 111, "2026-01-01T00:00:00+00:00") is True


def test_release_run_only_clears_its_own_pid(conn):
    make(conn)
    update_run(conn, "r-0001", state="running", pid=222)
    release_run(conn, "r-0001", 111)
    assert get_run(conn, "r-0001").pid == 222
    release_run(conn, "r-0001", 222)
    assert get_run(conn, "r-0001").pid is None
