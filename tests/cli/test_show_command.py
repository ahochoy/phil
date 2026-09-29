from typer.testing import CliRunner

from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
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


def test_show_unknown_run_fails(calc_repo):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "show", "r-ffff"])
    assert result.exit_code == 1
    assert "unknown run" in result.output
