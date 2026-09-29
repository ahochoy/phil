import json
import os
import subprocess
from pathlib import Path

import pytest

from phil.publish import publisher as publishing
from phil.publish.publisher import FakePublisher, GhPublisher, PrInfo, PublishError, PullRequest
from tests.helpers import run_git


def completed(args, code=0, out="", err=""):
    return subprocess.CompletedProcess(args, code, out, err)


class Runner:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        return self.replies.pop(0)


def test_available_explains_a_missing_gh(git_repo: Path):
    pub = GhPublisher(git_repo, runner=Runner([]), which=lambda name: None)
    assert "gh is not installed" in pub.available()


def test_available_explains_a_logged_out_gh(git_repo: Path):
    runner = Runner([completed(["gh"], code=1, err="You are not logged into any GitHub hosts")])
    pub = GhPublisher(git_repo, runner=runner, which=lambda name: "/usr/bin/gh")
    assert "gh auth login" in pub.available()


def test_available_needs_an_origin_remote(git_repo: Path):
    runner = Runner([completed(["gh"])])
    pub = GhPublisher(git_repo, runner=runner, which=lambda name: "/usr/bin/gh")
    assert "origin" in pub.available()


def test_available_never_raises_when_the_runner_itself_fails(git_repo: Path):
    def timed_out(args, stdin=None):
        raise PublishError("gh timed out")

    pub = GhPublisher(git_repo, runner=timed_out, which=lambda name: "/usr/bin/gh")
    assert "gh timed out" in pub.available()


def test_available_when_everything_is_set_up(git_repo: Path, tmp_path: Path):
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(git_repo, "remote", "add", "origin", str(remote))
    pub = GhPublisher(git_repo, runner=Runner([completed(["gh"])]), which=lambda name: "/usr/bin/gh")
    assert pub.available() is None


def test_push_sends_the_branch_to_origin(git_repo: Path, tmp_path: Path):
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(git_repo, "remote", "add", "origin", str(remote))
    run_git(git_repo, "branch", "phil/r-0001")
    GhPublisher(git_repo, runner=Runner([])).push("phil/r-0001")
    assert run_git(remote, "branch", "--list", "phil/r-0001").strip() == "phil/r-0001"


def test_push_failure_is_a_publish_error(git_repo: Path):
    with pytest.raises(PublishError):
        GhPublisher(git_repo, runner=Runner([])).push("phil/r-0001")  # no origin


def test_create_pr_passes_base_head_title_and_body_on_stdin(git_repo: Path):
    runner = Runner([completed(["gh"], out="https://github.com/o/r/pull/12\n")])
    pr = GhPublisher(git_repo, runner=runner).create_pr(branch="phil/r-0001", base="main", title="T", body="B")
    assert (pr.number, pr.url) == (12, "https://github.com/o/r/pull/12")
    args, stdin = runner.calls[0]
    assert args[:3] == ["gh", "pr", "create"]
    assert ["--base", "main"] == args[args.index("--base"):args.index("--base") + 2]
    assert ["--head", "phil/r-0001"] == args[args.index("--head"):args.index("--head") + 2]
    assert "--body-file" in args and stdin == "B"


def test_create_pr_failure_carries_gh_stderr(git_repo: Path):
    runner = Runner([completed(["gh"], code=1, err="a pull request already exists")])
    with pytest.raises(PublishError, match="already exists"):
        GhPublisher(git_repo, runner=runner).create_pr(branch="b", base="main", title="T", body="B")


URL = "https://github.com/o/r/pull/12"


@pytest.mark.parametrize("state, expected", [("OPEN", "open"), ("MERGED", "merged"), ("CLOSED", "closed")])
def test_pr_info_looks_the_pr_up_by_url(git_repo: Path, state: str, expected: str):
    reply = json.dumps({"state": state, "headRefName": "phil/r-0001", "headRefOid": "abc123"})
    runner = Runner([completed(["gh"], out=reply)])
    info = GhPublisher(git_repo, runner=runner).pr_info(URL)
    assert info == PrInfo(expected, "phil/r-0001", "abc123")
    assert runner.calls[0][0] == ["gh", "pr", "view", URL, "--json", "state,headRefName,headRefOid"]


def test_pr_info_rejects_unexpected_output(git_repo: Path):
    runner = Runner([completed(["gh"], out='{"state": "OPEN"}')])
    with pytest.raises(PublishError, match="unexpected output"):
        GhPublisher(git_repo, runner=runner).pr_info(URL)


def test_find_pr_returns_the_existing_pull_request(git_repo: Path):
    runner = Runner([completed(["gh"], out=json.dumps({"number": 12, "url": URL}))])
    assert GhPublisher(git_repo, runner=runner).find_pr("phil/r-0001") == PullRequest(12, URL)
    assert runner.calls[0][0] == ["gh", "pr", "view", "phil/r-0001", "--json", "number,url"]


def test_find_pr_is_none_when_gh_finds_nothing(git_repo: Path):
    runner = Runner([completed(["gh"], code=1, err="no pull requests found for branch")])
    assert GhPublisher(git_repo, runner=runner).find_pr("phil/r-0001") is None


def with_remote(git_repo: Path, tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(git_repo, "remote", "add", "origin", str(remote))
    return remote


def test_delete_remote_branch_tolerates_an_absent_branch(git_repo: Path, tmp_path: Path):
    with_remote(git_repo, tmp_path)
    assert GhPublisher(git_repo, runner=Runner([])).delete_remote_branch("phil/r-0001", None) is True


def test_delete_remote_branch_deletes_a_matching_branch(git_repo: Path, tmp_path: Path):
    remote = with_remote(git_repo, tmp_path)
    run_git(git_repo, "branch", "phil/r-0001")
    pub = GhPublisher(git_repo, runner=Runner([]))
    pub.push("phil/r-0001")
    oid = run_git(git_repo, "rev-parse", "phil/r-0001").strip()

    assert pub.delete_remote_branch("phil/r-0001", oid) is True

    assert run_git(remote, "branch", "--list", "phil/r-0001").strip() == ""


def test_delete_remote_branch_leaves_a_branch_that_moved(git_repo: Path, tmp_path: Path):
    remote = with_remote(git_repo, tmp_path)
    run_git(git_repo, "branch", "phil/r-0001")
    pub = GhPublisher(git_repo, runner=Runner([]))
    pub.push("phil/r-0001")
    ours = run_git(git_repo, "rev-parse", "phil/r-0001").strip()
    # Someone else's clone now owns the same branch name at another commit.
    run_git(git_repo, "commit", "--allow-empty", "-m", "theirs")
    run_git(git_repo, "push", "--force", "origin", "HEAD:refs/heads/phil/r-0001")
    theirs = run_git(git_repo, "rev-parse", "HEAD").strip()
    assert ours != theirs

    assert pub.delete_remote_branch("phil/r-0001", ours) is False

    assert run_git(remote, "rev-parse", "refs/heads/phil/r-0001").strip() == theirs


def test_delete_remote_branch_matches_the_full_ref_only(git_repo: Path, tmp_path: Path):
    remote = with_remote(git_repo, tmp_path)
    run_git(git_repo, "push", "origin", "HEAD:refs/heads/other/phil/r-0001")

    assert GhPublisher(git_repo, runner=Runner([])).delete_remote_branch("phil/r-0001", None) is True

    assert run_git(remote, "branch", "--list", "other/phil/r-0001").strip() == "other/phil/r-0001"


def test_network_git_commands_use_the_git_runner_with_prompts_off(git_repo: Path):
    git_calls = []

    def git_runner(args, stdin=None):
        git_calls.append(args)
        return completed(args)

    GhPublisher(git_repo, runner=Runner([]), git_runner=git_runner).push("phil/r-0001")
    assert git_calls == [["git", "push", "origin", "refs/heads/phil/r-0001:refs/heads/phil/r-0001"]]


def test_a_failing_git_runner_is_a_publish_error(git_repo: Path):
    def git_runner(args, stdin=None):
        return completed(args, code=128, err="fatal: could not read Username: terminal prompts disabled")

    with pytest.raises(PublishError, match="terminal prompts disabled"):
        GhPublisher(git_repo, runner=Runner([]), git_runner=git_runner).push("phil/r-0001")


def captured_run(monkeypatch, result=None, exc=None):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        if exc is not None:
            raise exc
        return result or completed(args)

    monkeypatch.setattr(publishing.subprocess, "run", fake_run)
    return seen


def test_git_commands_run_with_no_prompts_and_a_timeout(git_repo: Path, monkeypatch):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    seen = captured_run(monkeypatch)

    GhPublisher(git_repo).push("phil/r-0001")

    kwargs = seen["kwargs"]
    assert seen["args"][:2] == ["git", "push"]
    assert kwargs["timeout"] == publishing.GH_TIMEOUT_S
    assert kwargs["cwd"] == git_repo
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert kwargs["env"]["PATH"] == os.environ["PATH"]


def test_a_user_git_ssh_command_is_kept(git_repo: Path, monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i ~/.ssh/work")
    seen = captured_run(monkeypatch)

    GhPublisher(git_repo).push("phil/r-0001")

    assert seen["kwargs"]["env"]["GIT_SSH_COMMAND"] == "ssh -i ~/.ssh/work"


def test_gh_runs_with_prompts_disabled(git_repo: Path, monkeypatch):
    seen = captured_run(monkeypatch, result=completed(["gh"], out=URL + "\n"))

    GhPublisher(git_repo).create_pr(branch="b", base="main", title="T", body="B")

    env = seen["kwargs"]["env"]
    assert seen["args"][:3] == ["gh", "pr", "create"]
    assert (env["GH_PROMPT_DISABLED"], env["GIT_TERMINAL_PROMPT"]) == ("1", "0")
    assert seen["kwargs"]["timeout"] == publishing.GH_TIMEOUT_S


def test_a_git_timeout_is_a_publish_error(git_repo: Path, monkeypatch):
    captured_run(monkeypatch, exc=subprocess.TimeoutExpired(["git", "push"], publishing.GH_TIMEOUT_S))

    with pytest.raises(PublishError, match="timed out"):
        GhPublisher(git_repo).push("phil/r-0001")


def test_a_git_that_cannot_start_is_a_publish_error(git_repo: Path, monkeypatch):
    captured_run(monkeypatch, exc=FileNotFoundError("git"))

    with pytest.raises(PublishError, match="could not run git"):
        GhPublisher(git_repo).push("phil/r-0001")


def test_fake_publisher_records_and_fails_on_demand():
    fake = FakePublisher(states={12: "merged"}, fail={"push": "rejected"})
    with pytest.raises(PublishError, match="rejected"):
        fake.push("phil/r-0001")
    pr = fake.create_pr(branch="b", base="main", title="T", body="B")
    assert pr.number == 12
    assert fake.pr_info(pr.url) == PrInfo("merged", "b", "")


def test_tests_never_get_the_real_gh(git_repo: Path):
    # make_publisher is looked up through the module (not imported by name at module load),
    # so the autouse `no_real_gh` fixture's monkeypatch of `phil.publish.publisher.make_publisher`
    # actually takes effect here.
    assert publishing.make_publisher(git_repo).available() is not None
