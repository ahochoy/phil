from phil.agents.fake import ScriptedAgentFactory
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run
from phil.ui.show_view import render_show, show_refs
from phil.ui.theme import make_console
from tests.run.conftest import calc_plan, review, tester_report, write_green, write_red


def finished_run(calc_repo, *, depth: str | None = None):
    info = resolve_repo(calc_repo)
    kwargs = {} if depth is None else {"depth": depth}
    record = prepare_run(info, calc_plan(), info.head_sha, **kwargs)
    implementer = "quick_implementer" if depth == "quick" else "implementer"  # a quick run's own spec
    factory = ScriptedAgentFactory(
        {implementer: [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}
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


def test_render_show_reports_no_issues_recorded_for_a_malformed_json_file(calc_repo):
    info, record, paths = finished_run(calc_repo)
    (paths.run_dir(record.run_id) / "open_issues.json").write_text("{not valid json")
    conn = connect(paths.db_path)
    console = make_console(record=True, width=160)
    render_show(console, conn, paths, record.run_id)
    assert "none recorded" in console.export_text()


def test_render_show_header_shows_the_depth_when_the_run_has_one(calc_repo):
    info, record, paths = finished_run(calc_repo, depth="quick")
    conn = connect(paths.db_path)
    console = make_console(record=True, width=160)
    render_show(console, conn, paths, record.run_id)
    assert "· quick ·" in console.export_text()


def test_render_show_header_omits_depth_for_a_null_depth_run(calc_repo):
    # A run created before M3b (or by create_run with no `depth` arg) has depth=NULL.
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    create_run(
        conn, run_id="r-0001", keyword="CALC", base_sha=info.head_sha, worktree=paths.worktree_dir("r-0001"),
        tasks_total=1,
    )
    console = make_console(record=True, width=160)
    render_show(console, conn, paths, "r-0001")
    text = console.export_text()
    assert "· quick ·" not in text
    assert "· full ·" not in text


CHAT = "c-20261001-120000"


def _telemetry(conn, *, layer, role, node, at, run_id=None, chat_id=CHAT, tokens=(1000, 100), cost=0.5):
    conn.execute(
        "INSERT INTO telemetry (run_id, layer, node, role, model, attempt, packet_tokens, input_tokens,"
        " output_tokens, latency_ms, cost_usd, outcome, created_at, chat_id)"
        " VALUES (?, ?, ?, ?, 'm', 1, 0, ?, ?, 0, ?, 'ok', ?, ?)",
        (run_id, layer, node, role, tokens[0], tokens[1], cost, at, chat_id),
    )


def _chat_run(conn, paths, run_id, at, chat_id=CHAT):
    create_run(
        conn, run_id=run_id, keyword="CALC", base_sha="abc", worktree=paths.worktree_dir(run_id), tasks_total=1,
        chat_id=chat_id,
    )
    conn.execute("UPDATE runs SET created_at = ? WHERE run_id = ?", (at, run_id))


def _usage_rows(text: str) -> list[str]:
    return [line.split()[:2] for line in text.splitlines() if line.startswith(("chat ", "run "))]


def test_render_show_includes_the_chat_costs_of_the_goal_that_made_the_run(calc_repo):
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    # An earlier goal in the same chat, with its own run.
    _telemetry(conn, layer="chat", role="classifier", node="route", at="2026-10-01T10:00:00+00:00", cost=9.0)
    _telemetry(conn, layer="chat", role="architect", node="architect", at="2026-10-01T10:01:00+00:00", cost=9.0)
    _chat_run(conn, paths, "r-0001", "2026-10-01T10:02:00+00:00")
    # This goal: routed, taken in, planned and critiqued, then run.
    _telemetry(conn, layer="chat", role="classifier", node="route", at="2026-10-01T11:00:00+00:00")
    _telemetry(conn, layer="chat", role="orchestrator", node="intake", at="2026-10-01T11:01:00+00:00")
    _telemetry(conn, layer="chat", role="architect", node="architect", at="2026-10-01T11:02:00+00:00")
    _telemetry(conn, layer="chat", role="critic", node="critic", at="2026-10-01T11:03:00+00:00")
    _chat_run(conn, paths, "r-0002", "2026-10-01T11:04:00+00:00")
    _telemetry(conn, layer="run", role="implementer", node="implement", at="2026-10-01T11:05:00+00:00",
               run_id="r-0002", chat_id=None, tokens=(2000, 200), cost=1.0)
    # Chat rows after the run started belong to whatever the chat does next.
    _telemetry(conn, layer="chat", role="classifier", node="route", at="2026-10-01T11:06:00+00:00", cost=9.0)
    _telemetry(conn, layer="chat", role="architect", node="architect", at="2026-10-01T11:07:00+00:00",
               chat_id="c-other", cost=9.0)

    console = make_console(record=True, width=160)
    render_show(console, conn, paths, "r-0002")
    text = console.export_text()

    rows = _usage_rows(text)
    assert sorted(rows) == sorted([
        ["chat", "architect"], ["chat", "classifier"], ["chat", "critic"], ["chat", "orchestrator"],
        ["run", "implementer"],
    ])
    classifier = next(line for line in text.splitlines() if line.startswith("chat ") and "classifier" in line)
    assert classifier.split()[2] == "1"  # one call: the earlier goal's and the later route are left out
    # 4 chat rows of 1,100 tokens and $0.50, and the run's 2,200 tokens and $1.00.
    assert "Total: 6,600 tokens · $3.00" in text


def test_render_show_for_a_run_without_a_chat_shows_only_the_run(calc_repo):
    info, record, paths = finished_run(calc_repo)
    conn = connect(paths.db_path)
    _telemetry(conn, layer="chat", role="classifier", node="route", at="2000-01-01T00:00:00+00:00", chat_id=None)
    console = make_console(record=True, width=160)
    render_show(console, conn, paths, record.run_id)
    text = console.export_text()
    assert not any(row[0] == "chat" for row in _usage_rows(text))
    assert "Total: " not in text  # `phil run` output is unchanged


def test_render_show_leaves_out_side_questions_asked_before_the_goal(calc_repo):
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    # A question answered in the chat, and a /btw, before the goal was typed.
    _telemetry(conn, layer="chat", role="answerer", node="answer", at="2026-10-01T09:00:00+00:00", cost=9.0)
    _telemetry(conn, layer="chat", role="orchestrator", node="btw", at="2026-10-01T09:01:00+00:00", cost=9.0)
    _telemetry(conn, layer="chat", role="orchestrator", node="intake", at="2026-10-01T09:02:00+00:00")
    _telemetry(conn, layer="chat", role="architect", node="architect", at="2026-10-01T09:03:00+00:00")
    _chat_run(conn, paths, "r-0001", "2026-10-01T09:04:00+00:00")

    console = make_console(record=True, width=160)
    render_show(console, conn, paths, "r-0001")
    text = console.export_text()

    assert sorted(_usage_rows(text)) == [["chat", "architect"], ["chat", "orchestrator"]]
    orchestrator = next(line for line in text.splitlines() if line.startswith("chat ") and "orchestrator" in line)
    assert orchestrator.split()[2] == "1"  # intake only, not the /btw
    assert "answerer" not in text
    assert "Total: 2,200 tokens · $1.00" in text


def test_render_show_includes_a_design_row_for_the_goal(calc_repo):
    info = resolve_repo(calc_repo)
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    # This goal: routed, taken in, a design proposed, then planned.
    _telemetry(conn, layer="chat", role="orchestrator", node="intake", at="2026-10-01T09:00:00+00:00")
    _telemetry(conn, layer="chat", role="designer", node="design", at="2026-10-01T09:01:00+00:00")
    _telemetry(conn, layer="chat", role="architect", node="architect", at="2026-10-01T09:02:00+00:00")
    _chat_run(conn, paths, "r-0001", "2026-10-01T09:03:00+00:00")

    console = make_console(record=True, width=160)
    render_show(console, conn, paths, "r-0001")
    text = console.export_text()

    assert sorted(_usage_rows(text)) == [["chat", "architect"], ["chat", "designer"], ["chat", "orchestrator"]]
    assert "Total: 3,300 tokens · $1.50" in text


def test_show_refs_skips_a_file_deleted_between_listing_and_stat(calc_repo, monkeypatch):
    from pathlib import Path

    info, record, paths = finished_run(calc_repo)
    logs_dir = paths.run_dir(record.run_id) / "logs"
    (logs_dir / "gone.log").write_text("x\n")
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self.name == "gone.log":
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    labels = [r.label for r in show_refs(paths, record.run_id)]
    assert "test log gone" not in labels
    assert labels[0] == "summary"


def test_detail_text_caps_total_characters(tmp_path):
    from phil.ui.show_view import MAX_DETAIL_CHARS, detail_text

    path = tmp_path / "one-long-line.log"
    path.write_text("x" * (MAX_DETAIL_CHARS + 500))
    text = detail_text(path)
    assert text.startswith("x" * MAX_DETAIL_CHARS)
    assert "x" * (MAX_DETAIL_CHARS + 1) not in text
    assert text.endswith("… (500 more characters not shown)")
    assert MAX_DETAIL_CHARS == 200_000


def test_detail_text_strips_terminal_control_characters(tmp_path):
    from phil.ui.show_view import detail_text

    path = tmp_path / "evil.log"
    path.write_text(
        "ok\tline\n\x1b]0;pwned\x07\x1b[2Jclear\r\nbell\x07 del\x7f c1\x9b31m end\n", encoding="utf-8"
    )
    text = detail_text(path)
    assert "\x1b" not in text and "\x07" not in text and "\r" not in text
    assert "\x7f" not in text and "\x9b" not in text
    assert text.splitlines() == ["ok\tline", "]0;pwned[2Jclear", "bell del c131m end"]
