from datetime import datetime

import pytest

from phil.chat.overview import repo_overview
from phil.chat.session import ChatSession, list_open_chats
from phil.contracts import Goal
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run
from tests.helpers import run_git


def test_overview_lists_tracked_files_and_readme(git_repo):
    (git_repo / "README.md").write_text("# Demo\nA demo project.\n")
    (git_repo / "untracked.txt").write_text("x")
    run_git(git_repo, "add", "README.md")
    run_git(git_repo, "commit", "-m", "readme")
    text = repo_overview(git_repo)
    assert "README.md" in text
    assert "untracked.txt" not in text
    assert "A demo project." in text


def test_overview_caps_the_file_list(git_repo):
    for i in range(5):
        (git_repo / f"f{i}.py").write_text("")
    run_git(git_repo, "add", "-A")
    run_git(git_repo, "commit", "-m", "files")
    text = repo_overview(git_repo, max_files=2)
    assert "more files" in text


def test_session_records_raw_text_and_contracts(git_repo):
    paths = ProjectPaths(resolve_repo(git_repo).slug)
    session = ChatSession.create(paths, now=lambda: datetime(2026, 9, 24, 12, 0, 0))
    assert session.id == "c-20260924-120000"
    session.user("add  a *map*\n", stage="goal")
    session.contract("goal", Goal(objective="Add a map"))
    events, _ = session.transcript.read()
    assert events[0]["text"] == "add  a *map*\n"
    assert events[1]["contract"]["objective"] == "Add a map"
    again = ChatSession.create(paths, now=lambda: datetime(2026, 9, 24, 12, 0, 0))
    assert again.id == "c-20260924-120000-2"


def test_state_round_trip_and_open(git_repo):
    paths = ProjectPaths(resolve_repo(git_repo).slug)
    session = ChatSession.create(paths)
    assert session.load_state() == {}
    session.save_state({"stage": "approval", "goal": {"objective": "x"}})
    again = ChatSession.open(paths, session.id)
    assert again.load_state()["stage"] == "approval"


@pytest.mark.parametrize("chat_id", ["../../etc", "/tmp", "c-1/.."])
def test_open_rejects_ids_that_are_not_a_chat_id(git_repo, chat_id):
    paths = ProjectPaths(resolve_repo(git_repo).slug)
    with pytest.raises(ValueError):
        ChatSession.open(paths, chat_id)


def test_open_accepts_a_valid_chat_id(git_repo):
    paths = ProjectPaths(resolve_repo(git_repo).slug)
    session = ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 12, 0, 0))
    again = ChatSession.open(paths, session.id)
    assert again.id == session.id


def test_list_open_chats(git_repo):
    paths = ProjectPaths(resolve_repo(git_repo).slug)
    conn = connect(paths.db_path)
    running = ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 10, 0, 0))
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=paths.worktree_dir("r-0001"), tasks_total=1, chat_id=running.id)
    running.save_state({"stage": "running", "goal": {"objective": "Add divide"}, "run_id": "r-0001", "done_seen": False})
    finished = ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 11, 0, 0))
    finished.save_state({"stage": "idle", "goal": {"objective": "Old"}, "run_id": "r-0001", "done_seen": True})
    approving = ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 12, 0, 0))
    approving.save_state({"stage": "approval", "goal": {"objective": "Add pow"}, "plan": {"keyword": "POW"}})
    ChatSession.create(paths, now=lambda: datetime(2026, 9, 28, 13, 0, 0))  # empty chat, never had a goal
    chats = list_open_chats(paths, conn)
    assert [c.id for c in chats] == [approving.id, running.id]
    assert chats[1].objective == "Add divide" and chats[1].run_state == "pending"
