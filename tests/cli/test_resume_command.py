import pytest
from typer.testing import CliRunner

from phil.cli import main as cli
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import update_run
from tests.run.conftest import calc_plan

runner = CliRunner()


@pytest.fixture
def run(calc_repo, monkeypatch):
    info = resolve_repo(calc_repo)
    record = prepare_run(info, calc_plan(), info.head_sha)
    paths = ProjectPaths(info.slug)
    spawned = []
    monkeypatch.setattr(cli, "spawn_worker", lambda repo, run_id, mode, decision=None, **k: spawned.append((mode, decision)))
    return calc_repo, record.run_id, connect(paths.db_path), run_events(paths, record.run_id), spawned


def invoke(repo, *args):
    return runner.invoke(cli.app, ["--repo", str(repo), "resume", *args])


def escalate(conn, events, run_id):
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="escalated", needs_attention="CALC-001 failed 3 attempts in the green phase")
    events.append("escalation", escalation={"summary": "CALC-001 failed 3 attempts", "options": ["retry", "skip", "abort"]})


def test_escalated_run_needs_an_action(run):
    repo, run_id, conn, events, spawned = run
    escalate(conn, events, run_id)
    result = invoke(repo, run_id)
    assert result.exit_code == 2
    assert "retry, skip, abort" in result.output
    assert spawned == []


def test_escalated_run_resumes_with_action_and_hint(run):
    repo, run_id, conn, events, spawned = run
    escalate(conn, events, run_id)
    result = invoke(repo, run_id, "--action", "retry", "--hint", "use a minus sign")
    assert result.exit_code == 0, result.output
    assert spawned == [("resume", {"action": "retry", "hint": "use a minus sign"})]


def test_unknown_action_is_rejected(run):
    repo, run_id, conn, events, spawned = run
    escalate(conn, events, run_id)
    assert invoke(repo, run_id, "--action", "approve").exit_code == 2
    assert spawned == []


def test_failed_run_continues(run):
    repo, run_id, conn, events, spawned = run
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="failed", needs_attention="worker failed: RuntimeError: x")
    assert invoke(repo, run_id).exit_code == 0
    assert spawned == [("continue", None)]
    assert invoke(repo, run_id, "--action", "retry").exit_code == 2


def test_finished_or_pending_runs_are_not_resumed(run):
    repo, run_id, conn, events, spawned = run
    result = invoke(repo, run_id)
    assert result.exit_code == 1
    assert "still starting" in result.output
    assert invoke(repo, "r-ffff").exit_code == 1


def test_pending_run_still_starting_is_not_resumed(run):
    repo, run_id, conn, events, spawned = run
    result = invoke(repo, run_id)
    assert result.exit_code == 1
    assert "still starting" in result.output
    assert f"phil attach {run_id}" in result.output
    assert spawned == []
    assert invoke(repo, run_id, "--action", "retry").exit_code == 2


def test_stale_pending_run_continues(run):
    repo, run_id, conn, events, spawned = run
    conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", ("2000-01-01T00:00:00+00:00", run_id))
    result = invoke(repo, run_id)
    assert result.exit_code == 0, result.output
    assert spawned == [("continue", None)]
    assert invoke(repo, run_id, "--action", "retry").exit_code == 2
