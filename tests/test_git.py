import pickle

import pytest

from phil.git import GitError, branch_for, git


def test_git_returns_stdout(git_repo):
    out = git(git_repo, "rev-parse", "HEAD")
    assert len(out.strip()) == 40


def test_git_error_includes_command_and_stderr(git_repo):
    with pytest.raises(GitError) as excinfo:
        git(git_repo, "not-a-real-subcommand")
    message = str(excinfo.value)
    assert "not-a-real-subcommand" in message
    assert excinfo.value.stderr in message
    assert excinfo.value.stderr


def test_branch_for_valid_run_id():
    assert branch_for("r-7f3a") == "phil/r-7f3a"


def test_branch_for_rejects_invalid_run_id():
    with pytest.raises(ValueError):
        branch_for("bad")


def test_git_error_exposes_command(git_repo):
    with pytest.raises(GitError) as excinfo:
        git(git_repo, "not-a-real-subcommand")
    assert excinfo.value.command == ["not-a-real-subcommand"]


def test_git_error_round_trips_through_pickle():
    error = GitError(["status"], 128, "fatal: not a git repository")
    restored = pickle.loads(pickle.dumps(error))
    assert restored.command == ["status"]
    assert restored.returncode == 128
    assert restored.stderr == "fatal: not a git repository"
    assert str(restored) == str(error)


def test_git_error_repr_keeps_details():
    assert "128" in repr(GitError(["status"], 128, "boom"))
