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
