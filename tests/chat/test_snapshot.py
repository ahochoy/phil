import shutil

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


PLANTED = "sk-PLANTED-not-a-real-secret-0042"


def _all_text(tree) -> str:
    return "".join(p.read_text(errors="replace") for p in tree.rglob("*") if p.is_file())


def test_export_worktree_has_the_working_tree_without_ignored_files(calc_repo, tmp_path):
    from phil.chat.snapshot import export_worktree

    (calc_repo / ".gitignore").write_text(".env\n")
    (calc_repo / ".env").write_text(f"OPENROUTER_API_KEY={PLANTED}\n")
    (calc_repo / "calc.py").write_text("uncommitted change\n")
    (calc_repo / "notes.md").write_text("untracked, not ignored\n")
    tree = export_worktree(calc_repo, tmp_path / "snap")
    assert tree == tmp_path / "snap"
    assert (tree / "calc.py").read_text() == "uncommitted change\n"  # the edit's new content
    assert (tree / "notes.md").read_text() == "untracked, not ignored\n"
    assert (tree / "tests" / "test_calc.py").exists()
    assert (tree / ".gitignore").exists()
    assert not (tree / ".env").exists()
    assert not (tree / ".git").exists()
    assert PLANTED not in _all_text(tree)


def test_export_worktree_skips_deleted_files_and_links_out_of_the_repo(calc_repo, tmp_path):
    from phil.chat.snapshot import export_worktree

    (calc_repo / ".gitignore").write_text(".env\n")
    (calc_repo / ".env").write_text(f"KEY={PLANTED}\n")
    outside = tmp_path / "outside.txt"
    outside.write_text(f"outside {PLANTED}\n")
    (calc_repo / "abs").symlink_to(outside)
    (calc_repo / "up").symlink_to("../outside.txt")
    (calc_repo / "to_env").symlink_to(".env")  # inside the repo, but at an ignored file
    (calc_repo / "inside").symlink_to("calc.py")
    (calc_repo / "tests" / "test_calc.py").unlink()  # tracked, deleted in the working tree
    tree = export_worktree(calc_repo, tmp_path / "snap")
    assert not (tree / "tests" / "test_calc.py").exists()
    assert not (tree / "abs").exists() and not (tree / "abs").is_symlink()
    assert not (tree / "up").exists() and not (tree / "up").is_symlink()
    assert not (tree / "to_env").exists()  # dangling at most: the snapshot has no .env
    assert (tree / "inside").read_text() == "def add(a, b):\n    return a + b\n"
    assert PLANTED not in _all_text(tree)


def test_export_worktree_replaces_an_earlier_export(calc_repo, tmp_path):
    from phil.chat.snapshot import export_worktree

    dest = tmp_path / "snap"
    dest.mkdir()
    (dest / "leftover.txt").write_text("stale")
    export_worktree(calc_repo, dest)
    assert not (dest / "leftover.txt").exists()
    assert (dest / "calc.py").exists()


def test_export_worktree_skips_files_under_a_directory_linked_out_of_the_repo(calc_repo, tmp_path):
    from phil.chat.snapshot import export_worktree

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "test_calc.py").write_text(f"{PLANTED}\n")
    shutil.rmtree(calc_repo / "tests")
    (calc_repo / "tests").symlink_to(elsewhere)  # tracked tests/test_calc.py now resolves outside
    tree = export_worktree(calc_repo, tmp_path / "snap")
    assert not (tree / "tests" / "test_calc.py").exists()
    assert PLANTED not in _all_text(tree)
