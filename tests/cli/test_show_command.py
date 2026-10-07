from typer.testing import CliRunner

from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
from phil.store.activity import activity_log
from phil.store.paths import ProjectPaths
from tests.run.conftest import calc_plan, review, tester_report, write_green, write_red

runner = CliRunner()


def finished_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    factory = ScriptedAgentFactory(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    run_worker(calc_repo, record.run_id, "start", factory=factory)
    return info, record, ProjectPaths(info.slug)


def test_show_prints_the_header_usage_and_a_numbered_summary_ref(calc_repo):
    info, record, paths = finished_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id])
    assert result.exit_code == 0, result.output
    assert f"Run {record.run_id}" in result.output
    assert "CALC" in result.output
    assert "completed" in result.output
    assert "implementer" in result.output
    assert "1 summary" in result.output


def test_show_n_prints_the_referenced_detail_in_full(calc_repo):
    info, record, paths = finished_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id, "1"])
    assert result.exit_code == 0, result.output
    assert f"# Run {record.run_id} · CALC" in result.output
    assert "## Usage" in result.output


def test_show_n_pretty_prints_a_json_detail(calc_repo):
    info, record, paths = finished_run(calc_repo)
    from phil.ui.show_view import show_refs

    refs = show_refs(paths, record.run_id)
    n = next(i for i, ref in enumerate(refs, start=1) if ref.label.startswith("packet "))
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id, str(n)])
    assert result.exit_code == 0, result.output
    assert '"contract_type"' in result.output or "{" in result.output


def test_show_unknown_detail_number_fails(calc_repo):
    info, record, paths = finished_run(calc_repo)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id, "99"])
    assert result.exit_code == 1
    assert f"No detail #99 for {record.run_id}." in result.output


def test_show_step_prints_an_activity_detail(calc_repo):
    info, record, paths = finished_run(calc_repo)
    log = activity_log(paths, record.run_id)
    log.end(
        14, task="CALC-001", role="implementer", tool="run_shell", summary="run pytest -q",
        result="→ 2 failed", ok=False, detail="2 failed\n", duration_ms=1200,
    )
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id, "--step", "14"])
    assert result.exit_code == 0, result.output
    assert "2 failed" in result.output

    # A second, never-started run has no activity log at all: step 14 is unknown there.
    other_info = resolve_repo(calc_repo)
    other_record = prepare_run(other_info, calc_plan(), other_info.head_sha)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", other_record.run_id, "--step", "14"])
    assert result.exit_code == 1
    assert "#14 has no details." in result.output


def test_show_unknown_run_fails(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", "r-ffff"])
    assert result.exit_code == 1
    assert "unknown run" in result.output


def test_show_n_strips_terminal_control_characters(calc_repo):
    info, record, paths = finished_run(calc_repo)
    (paths.run_dir(record.run_id) / "summary.md").write_text("# Summary\n\x1b[2J\x1b]0;pwned\x07done\n")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id, "1"])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output and "\x07" not in result.output
    assert "[2J]0;pwneddone" in result.output


def test_show_n_reports_a_detail_that_vanished(calc_repo, monkeypatch):
    info, record, paths = finished_run(calc_repo)
    from phil.ui import show_view

    def vanished(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(show_view, "detail_text", vanished)
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", record.run_id, "1"])
    assert result.exit_code == 1
    assert "Couldn't read" in result.output
