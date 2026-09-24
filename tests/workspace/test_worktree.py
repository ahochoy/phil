import pytest

from phil.workspace.worktree import WorktreeManager
from tests.helpers import run_git


@pytest.fixture
def setup(git_repo, tmp_path):
    manager = WorktreeManager(git_repo)
    base = run_git(git_repo, "rev-parse", "HEAD").strip()
    worktree = manager.create(run_id="r-0001", base_sha=base, path=tmp_path / "wt" / "r-0001")
    return manager, worktree, base


def test_create_makes_branch_and_checkout(setup, git_repo):
    manager, worktree, base = setup
    assert worktree.branch == "phil/r-0001"
    assert (worktree.path / "app.py").exists()
    assert "phil/r-0001" in run_git(git_repo, "branch", "--list", "phil/r-0001")
    assert manager.head(worktree.path) == base


def test_user_checkout_is_untouched(setup, git_repo):
    manager, worktree, _ = setup
    (worktree.path / "app.py").write_text("changed in worktree\n")
    assert run_git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert (git_repo / "app.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_changed_files_includes_modified_and_untracked(setup):
    manager, worktree, base = setup
    (worktree.path / "app.py").write_text("changed\n")
    (worktree.path / "tests").mkdir()
    (worktree.path / "tests" / "test_app.py").write_text("def test_x(): pass\n")
    assert manager.changed_files(worktree.path, since=base) == ["app.py", "tests/test_app.py"]


def test_commit_all_advances_branch(setup, git_repo):
    manager, worktree, base = setup
    (worktree.path / "new.py").write_text("x = 1\n")
    sha = manager.commit_all(worktree.path, "MAPS-001: add new")
    assert sha != base
    assert run_git(git_repo, "rev-parse", "phil/r-0001").strip() == sha
    assert "new.py" in manager.diff(worktree.path, base)
    assert manager.changed_files(worktree.path, since=sha) == []


def test_remove_deletes_worktree_and_branch(setup, git_repo):
    manager, worktree, _ = setup
    manager.remove(worktree)
    assert not worktree.path.exists()
    assert run_git(git_repo, "branch", "--list", "phil/r-0001").strip() == ""


def test_remove_can_keep_branch(setup, git_repo):
    manager, worktree, _ = setup
    manager.remove(worktree, delete_branch=False)
    assert not worktree.path.exists()
    assert "phil/r-0001" in run_git(git_repo, "branch", "--list", "phil/r-0001")
