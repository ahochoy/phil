from typer.testing import CliRunner

from phil.cli import main as cli
from phil.publish.publisher import FakePublisher
from tests.cli.test_diff_clean import finished_run

runner = CliRunner()


def test_pr_command_opens_a_pull_request(calc_repo, monkeypatch):
    info, record, paths = finished_run(calc_repo)
    fake = FakePublisher()
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "pr", record.run_id])

    assert result.exit_code == 0, result.output
    assert "Opened PR #12" in result.output


def test_pr_command_refuses_when_gh_is_disabled_in_tests(calc_repo):
    info, record, paths = finished_run(calc_repo)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "pr", record.run_id])

    assert result.exit_code == 1
    assert "gh is disabled in tests" in result.output
