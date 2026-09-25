from datetime import datetime

from phil.chat.overview import repo_overview
from phil.chat.session import ChatSession
from phil.contracts import Goal
from phil.repo import resolve_repo
from phil.store.paths import ProjectPaths
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
