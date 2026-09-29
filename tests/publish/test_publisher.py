import subprocess
from pathlib import Path

import pytest

from phil.publish import publisher as publishing
from phil.publish.publisher import FakePublisher, GhPublisher, PublishError
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


@pytest.mark.parametrize("reply, state", [('{"state": "OPEN"}', "open"), ('{"state": "MERGED"}', "merged"),
                                          ('{"state": "CLOSED"}', "closed")])
def test_pr_state_maps_gh_json(git_repo: Path, reply: str, state: str):
    runner = Runner([completed(["gh"], out=reply)])
    assert GhPublisher(git_repo, runner=runner).pr_state(12) == state
    assert runner.calls[0][0][:4] == ["gh", "pr", "view", "12"]


def test_delete_remote_branch_tolerates_an_absent_branch(git_repo: Path, tmp_path: Path):
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(git_repo, "remote", "add", "origin", str(remote))
    GhPublisher(git_repo, runner=Runner([])).delete_remote_branch("phil/r-0001")  # no error


def test_fake_publisher_records_and_fails_on_demand():
    fake = FakePublisher(states={12: "merged"}, fail={"push": "rejected"})
    with pytest.raises(PublishError, match="rejected"):
        fake.push("phil/r-0001")
    assert fake.create_pr(branch="b", base="main", title="T", body="B").number == 12
    assert fake.pr_state(12) == "merged"


def test_tests_never_get_the_real_gh(git_repo: Path):
    # make_publisher is looked up through the module (not imported by name at module load),
    # so the autouse `no_real_gh` fixture's monkeypatch of `phil.publish.publisher.make_publisher`
    # actually takes effect here.
    assert publishing.make_publisher(git_repo).available() is not None
