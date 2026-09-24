import re

import pytest

from phil.repo import NoCommits, NotAGitRepo, resolve_repo
from tests.helpers import run_git


def test_resolves_root_from_subdirectory(git_repo):
    sub = git_repo / "src"
    sub.mkdir()
    info = resolve_repo(sub)
    assert info.root == git_repo
    assert info.branch == "main"
    assert info.head_sha == run_git(git_repo, "rev-parse", "HEAD").strip()


def test_slug_is_name_plus_path_hash(git_repo):
    info = resolve_repo(git_repo)
    assert re.fullmatch(r"target-[0-9a-f]{8}", info.slug)
    assert resolve_repo(git_repo).slug == info.slug


def test_reports_dirty_files(git_repo):
    (git_repo / "app.py").write_text("changed\n")
    (git_repo / "notes.txt").write_text("todo\n")
    info = resolve_repo(git_repo)
    assert info.dirty_files == ["app.py", "notes.txt"]


def test_clean_repo_has_no_dirty_files(git_repo):
    assert resolve_repo(git_repo).dirty_files == []


def test_non_repo_raises(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(NotAGitRepo):
        resolve_repo(plain)


def test_repo_without_commits_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    run_git(empty, "init", "-b", "main")
    with pytest.raises(NoCommits):
        resolve_repo(empty)
