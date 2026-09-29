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


def _published(calc_repo):
    from phil.publish.service import publish_run
    from phil.store.db import connect
    from phil.store.runs import get_run, update_run

    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    fake = FakePublisher()
    record = publish_run(info, conn, get_run(conn, record.run_id), fake)
    update_run(conn, record.run_id, pr_checked_at="2000-01-01T00:00:00+00:00")
    return record, paths, fake


def test_runs_notices_a_merged_pr_and_cleans_up(calc_repo, monkeypatch):
    record, paths, fake = _published(calc_repo)
    fake.states[12] = "merged"
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "runs"])

    assert result.exit_code == 0, result.output
    assert f"{record.run_id} merged (#12); cleaned up." in result.output
    assert "merged #12" in result.output


def test_show_notices_a_closed_pr(calc_repo, monkeypatch):
    record, paths, fake = _published(calc_repo)
    fake.states[12] = "closed"
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id])

    assert result.exit_code == 0, result.output
    assert "PR #12 was closed without merging" in result.output


def test_runs_still_works_when_the_sweep_blows_up(calc_repo, monkeypatch):
    record, paths, fake = _published(calc_repo)

    def explode(root):
        raise RuntimeError("no network")

    monkeypatch.setattr("phil.publish.publisher.make_publisher", explode)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "runs"])

    assert result.exit_code == 0, result.output
    assert record.run_id in result.output


def test_clean_merged_cleans_merged_runs_even_when_recently_checked(calc_repo, monkeypatch):
    from phil.store.db import connect, utcnow
    from phil.store.runs import get_run, update_run

    record, paths, fake = _published(calc_repo)
    update_run(connect(paths.db_path), record.run_id, pr_checked_at=utcnow())
    fake.states[12] = "merged"
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", "--merged"])

    assert result.exit_code == 0, result.output
    assert f"{record.run_id} merged (#12); cleaned up." in result.output
    assert get_run(connect(paths.db_path), record.run_id).state == "cleaned"


def test_clean_merged_with_nothing_to_do(calc_repo, monkeypatch):
    record, paths, fake = _published(calc_repo)
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", "--merged"])

    assert result.exit_code == 0, result.output
    assert "No merged pull requests to clean up." in result.output


def test_clean_needs_a_run_id_or_merged(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean"])
    assert result.exit_code == 2
    assert "phil clean <run-id> or phil clean --merged" in result.output


def test_clean_refuses_a_run_id_with_merged(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "clean", "r-1234", "--merged"])
    assert result.exit_code == 2
    assert "phil clean <run-id> or phil clean --merged" in result.output
