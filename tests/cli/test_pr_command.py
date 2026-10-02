from typer.testing import CliRunner

from phil.cli import main as cli
from phil.publish.publisher import FakePublisher
from tests.cli.test_diff_clean import finished_run, incomplete_run

runner = CliRunner()


def test_pr_command_opens_a_pull_request(calc_repo, monkeypatch):
    info, record, paths = finished_run(calc_repo)
    fake = FakePublisher()
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "pr", record.run_id])

    assert result.exit_code == 0, result.output
    assert "Opened PR #12" in result.output


def test_pr_command_refuses_an_incomplete_run(calc_repo, monkeypatch):
    info, record, paths = incomplete_run(calc_repo)
    fake = FakePublisher()
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "pr", record.run_id])

    assert result.exit_code == 1
    output = " ".join(result.output.split())  # the console may wrap the long line
    assert f"{record.run_id} finished with blocking issues open; there's nothing to open a PR for." in output
    assert f"Use phil pr {record.run_id} --force to open it anyway." in output
    assert fake.calls == []


def test_pr_force_publishes_an_incomplete_run_after_listing_its_open_issues(calc_repo, monkeypatch):
    info, record, paths = incomplete_run(calc_repo)
    fake = FakePublisher()
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "pr", record.run_id, "--force"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    start = lines.index("Publishing an incomplete run; open issues:")
    assert lines[start + 1] == "- (major) subtract is untested for negatives"
    assert "Opened PR #12" in result.output
    assert ("push", record.branch) in fake.calls


def test_pr_command_refuses_a_run_with_no_commits(calc_repo, monkeypatch):
    from tests.publish.test_service_publish import without_commits

    info, record, paths = finished_run(calc_repo)
    without_commits(record)
    fake = FakePublisher()
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "pr", record.run_id])

    assert result.exit_code == 1
    assert f"{record.run_id} has no commits to open a PR for." in result.output
    assert fake.calls == []


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


def test_a_failing_sweep_is_logged_as_a_warning_and_never_printed(calc_repo, monkeypatch, caplog):
    record, paths, fake = _published(calc_repo)

    def boom(*args, **kwargs):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)
    monkeypatch.setattr("phil.publish.service.sweep_prs", boom)
    with caplog.at_level("WARNING", logger="phil"):
        result = runner.invoke(cli.app, ["--repo", str(calc_repo), "runs"])

    assert result.exit_code == 0, result.output
    assert "sweep exploded" not in result.output
    assert any(r.levelname == "WARNING" and r.name.startswith("phil") and "sweep" in r.getMessage()
               for r in caplog.records)


def test_a_failing_sweep_does_not_reach_the_last_resort_handler(calc_repo, monkeypatch):
    import logging

    record, paths, fake = _published(calc_repo)
    last_resort = []

    class Recorder(logging.Handler):
        def emit(self, record):
            last_resort.append(record)

    def boom(*args, **kwargs):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)
    monkeypatch.setattr("phil.publish.service.sweep_prs", boom)
    monkeypatch.setattr(logging.root, "handlers", [])  # as outside pytest: no handlers configured
    monkeypatch.setattr(logging, "lastResort", Recorder(logging.WARNING))

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "runs"])

    assert result.exit_code == 0, result.output
    assert last_resort == []


def test_a_sweep_warning_prints_no_traceback(calc_repo, monkeypatch):
    import logging

    record, paths, fake = _published(calc_repo)

    def pr_info(url):
        raise RuntimeError("gh output changed")  # the sweep logs this at warning with a traceback

    fake.pr_info = pr_info
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda root: fake)
    monkeypatch.setattr(logging.root, "handlers", [])  # as outside pytest: only the last-resort handler

    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "runs"])

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.stderr
    assert "gh output changed" not in result.output
