import pytest

from phil.chat.snapshot import export_tree
from phil.git import GitError
from tests.helpers import run_git


def test_export_tree_has_only_committed_tracked_files(calc_repo, tmp_path):
    (calc_repo / "secret.env").write_text("TOKEN=x\n")
    (calc_repo / "calc.py").write_text("uncommitted change\n")
    sha = run_git(calc_repo, "rev-parse", "HEAD").strip()
    tree = export_tree(calc_repo, sha, tmp_path / "snap")
    assert tree == tmp_path / "snap"
    assert not (tree / "secret.env").exists()
    assert (tree / "tests" / "test_calc.py").exists()
    assert (tree / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_export_tree_reuses_a_complete_export(calc_repo, tmp_path):
    sha = run_git(calc_repo, "rev-parse", "HEAD").strip()
    tree = export_tree(calc_repo, sha, tmp_path / "snap")
    (tree / "calc.py").write_text("touched\n")
    again = export_tree(calc_repo, sha, tmp_path / "snap")
    assert again == tree
    assert (tree / "calc.py").read_text() == "touched\n"


def test_export_tree_redoes_an_incomplete_export(calc_repo, tmp_path):
    sha = run_git(calc_repo, "rev-parse", "HEAD").strip()
    dest = tmp_path / "snap"
    dest.mkdir()
    (dest / "leftover.txt").write_text("partial")
    export_tree(calc_repo, sha, dest)
    assert not (dest / "leftover.txt").exists()
    assert (dest / "calc.py").exists()


def test_export_tree_rejects_a_bad_sha(calc_repo, tmp_path):
    with pytest.raises(GitError):
        export_tree(calc_repo, "0" * 40, tmp_path / "snap")


def test_export_tree_skips_links_that_leave_the_snapshot(calc_repo, tmp_path):
    (calc_repo / "abs").symlink_to("/etc/hosts")
    (calc_repo / "up").symlink_to("../outside.txt")
    (calc_repo / "inside").symlink_to("calc.py")
    run_git(calc_repo, "add", "abs", "up", "inside")
    run_git(calc_repo, "commit", "-m", "links")
    sha = run_git(calc_repo, "rev-parse", "HEAD").strip()
    tree = export_tree(calc_repo, sha, tmp_path / "snap")
    assert not (tree / "abs").is_symlink() and not (tree / "abs").exists()
    assert not (tree / "up").is_symlink() and not (tree / "up").exists()
    assert (tree / "inside").read_text() == "def add(a, b):\n    return a + b\n"
    assert (tree / "calc.py").exists()
