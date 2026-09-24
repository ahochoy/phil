from typer.testing import CliRunner

from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.cli.attach import AttachIO, attach, render_event
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.ui.theme import make_console
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red


def escalated_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    run_worker(calc_repo, record.run_id, "start", factory=ScriptedAgentFactory({"implementer": [write_red, bad_green, bad_green, bad_green]}))
    return info, record


def finishing():
    return ScriptedAgentFactory({"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]})


def test_attach_answers_an_escalation_and_follows_to_the_end(calc_repo):
    info, record = escalated_run(calc_repo)
    paths = ProjectPaths(info.slug)
    asked = []
    io = AttachIO(
        choose=lambda prompt, options: asked.append((prompt, options)) or "retry",
        ask_hint=lambda: "use a minus sign",
        spawn=lambda mode, decision: run_worker(calc_repo, record.run_id, mode, decision, factory=finishing()),
        sleep=lambda _: None,
    )
    console = make_console(record=True, width=120)
    state = attach(connect(paths.db_path), record.run_id, run_events(paths, record.run_id), console, io, poll_s=0)
    assert state == "completed"
    assert asked[0][1] == ["retry", "skip", "abort"]
    text = console.export_text()
    assert "CALC-001 failed 3 attempts in the green phase" in text
    assert "summary.md" in text


def test_attach_stops_at_a_failed_run(calc_repo):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    try:
        run_worker(calc_repo, record.run_id, "start", factory=ScriptedAgentFactory({"implementer": [RuntimeError("boom")]}))
    except RuntimeError:
        pass
    paths = ProjectPaths(info.slug)
    console = make_console(record=True, width=120)
    io = AttachIO(choose=lambda p, o: "abort", ask_hint=lambda: None, spawn=lambda m, d: None, sleep=lambda _: None)
    assert attach(connect(paths.db_path), record.run_id, run_events(paths, record.run_id), console, io, poll_s=0) == "failed"
    assert f"phil resume {record.run_id}" in console.export_text()


def test_attach_command_prompts_for_a_decision(calc_repo, monkeypatch):
    info, record = escalated_run(calc_repo)
    monkeypatch.setattr(
        cli, "spawn_worker",
        lambda repo, run_id, mode, decision=None, **k: run_worker(repo, run_id, mode, decision, factory=finishing()),
    )
    result = CliRunner().invoke(cli.app, ["--repo", str(calc_repo), "attach", record.run_id], input="retry\nuse a minus sign\n")
    assert result.exit_code == 0, result.output
    assert get_run(connect(ProjectPaths(info.slug).db_path), record.run_id).state == "completed"


def test_render_event_handles_the_spawn_kind_without_raising():
    console = make_console(record=True, width=120)
    render_event(console, {"kind": "spawn", "pid": 4242, "mode": "resume"})
    text = console.export_text()
    assert "4242" in text
    assert "resume" in text
