from phil.agents.fake import ScriptedAgentFactory
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.ui.show_view import render_show, show_refs
from phil.ui.theme import make_console
from tests.run.conftest import calc_plan, review, tester_report, write_green, write_red


def finished_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    factory = ScriptedAgentFactory(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
    )
    run_worker(calc_repo, record.run_id, "start", factory=factory)
    return info, record, ProjectPaths(info.slug)


def test_show_refs_is_empty_for_a_run_with_no_files(calc_repo):
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    assert show_refs(paths, "r-0000") == []


def test_show_refs_lists_summary_first_then_test_logs_outputs_and_packets(calc_repo):
    info, record, paths = finished_run(calc_repo)
    refs = show_refs(paths, record.run_id)
    assert refs[0].label == "summary"
    # No worker.log was written (run_worker was called in-process, not through spawn_worker).
    assert "worker log" not in [r.label for r in refs]
    labels = [r.label for r in refs]
    assert sum(label.startswith("test log ") for label in labels) <= 5
    assert any(label.startswith("test log ") for label in labels)
    assert any("tester" in label for label in labels if label.startswith("output "))
    assert any("review" in label for label in labels if label.startswith("output "))
    assert sum(label.startswith("packet ") for label in labels) == 4


def test_show_refs_includes_the_worker_log_right_after_the_summary_when_present(calc_repo):
    info, record, paths = finished_run(calc_repo)
    log_path = paths.run_dir(record.run_id) / "logs" / "worker.log"
    log_path.write_text("hello\n")
    refs = show_refs(paths, record.run_id)
    assert [refs[0].label, refs[1].label] == ["summary", "worker log"]


def test_show_refs_caps_each_category_at_five(calc_repo):
    info, record, paths = finished_run(calc_repo)
    logs_dir = paths.run_dir(record.run_id) / "logs"
    for i in range(10):
        (logs_dir / f"extra-{i}.log").write_text("x\n")
    refs = show_refs(paths, record.run_id)
    labels = [r.label for r in refs]
    assert sum(label.startswith("test log ") for label in labels) == 5


def test_render_show_prints_header_usage_and_open_issues(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    console = make_console(record=True, width=160)
    refs = render_show(console, conn, paths, record.run_id)
    text = console.export_text()
    assert f"Run {record.run_id}" in text
    assert "CALC" in text
    assert "completed" in text
    assert "1/1 tasks" in text
    assert "CALC-001" in text
    assert "implementer" in text
    assert "tester" in text
    assert "reviewer" in text
    assert "(none)" in text
    assert "1 summary" in text
    assert refs and refs[0].label == "summary"


def test_render_show_reports_no_issues_recorded_for_an_older_run_without_the_json_file(calc_repo):
    info, record, paths = finished_run(calc_repo)
    (paths.run_dir(record.run_id) / "open_issues.json").unlink()
    conn = connect(paths.db_path)
    console = make_console(record=True, width=160)
    render_show(console, conn, paths, record.run_id)
    assert "none recorded" in console.export_text()
