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
