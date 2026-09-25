from phil.agents.fake import ScriptedAgentFactory
from phil.chat.controller import ChatController, ChatIO
from phil.config import PhilConfig
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from phil.ui.theme import make_console
from tests.chat.conftest import critique, goal, plan
from tests.helpers import TEST_MODELS


def run_chat(repo, answers, scripts, config=None):
    info = resolve_repo(repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = list(answers)
    spawned = []
    io = ChatIO(ask=lambda prompt: queue.pop(0) if queue else None, spawn=lambda root, run_id, mode: spawned.append((run_id, mode)))
    factory = ScriptedAgentFactory(scripts)
    ChatController(info, config or PhilConfig(models=TEST_MODELS), conn, console, io, factory=factory).run()
    return console.export_text(), spawned, list_runs(conn), factory


def test_goal_to_approved_run(calc_repo):
    text, spawned, runs, factory = run_chat(
        calc_repo,
        ["add subtract", "y"],
        {"intake": [goal()], "architect": [plan()], "critic": [critique(notes=["looks fine"])]},
    )
    assert [r.run_id for r in runs] == [spawned[0][0]]
    assert spawned[0][1] == "start"
    assert runs[0].keyword == "CALC"
    assert "Plan CALC v1" in text and "looks fine" in text
    assert f"phil attach {spawned[0][0]}" in text
    assert factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_clarifying_questions_are_asked_once_answered(calc_repo):
    text, spawned, runs, factory = run_chat(
        calc_repo,
        ["add subtract", "in calc.py", "n"],
        {"intake": [goal(open_questions=["Which module?"]), goal()], "architect": [plan()], "critic": [critique()]},
    )
    assert "Which module?" in text
    assert "Plan dropped." in text
    assert spawned == [] and runs == []


def test_edit_revises_the_plan(calc_repo):
    text, spawned, runs, factory = run_chat(
        calc_repo,
        ["add subtract", "edit", "make it two tasks", "y"],
        {"intake": [goal()], "architect": [plan(), plan(n=2)], "critic": [critique(), critique()]},
    )
    assert "Plan CALC v2 · 2 tasks" in text
    assert runs[0].tasks_total == 2


def test_missing_run_models_block_approval(calc_repo):
    config = PhilConfig(models={r: m for r, m in TEST_MODELS.items() if r in ("orchestrator", "architect", "critic")})
    text, spawned, runs, _ = run_chat(
        calc_repo, ["add subtract", "y", "n"],
        {"intake": [goal()], "architect": [plan()], "critic": [critique()]}, config=config,
    )
    assert "implementer, tester, reviewer" in text
    assert spawned == [] and runs == []


def test_bad_test_command_blocks_approval(calc_repo):
    text, spawned, runs, _ = run_chat(
        calc_repo, ["add subtract", "y", "n"],
        {"intake": [goal()], "architect": [plan(test_cmd="pytest; rm -rf /")], "critic": [critique()]},
    )
    assert "shell operators" in text
    assert spawned == [] and runs == []


def test_agent_failure_returns_to_the_prompt(calc_repo):
    text, spawned, runs, _ = run_chat(
        calc_repo, ["add subtract", "/help"],
        {"intake": [RuntimeError("provider down")]},
    )
    assert "couldn't finish that" in text and "provider down" in text
    assert "/runs" in text  # help printed after the failure: the loop kept going


def test_slash_commands(calc_repo):
    text, *_ = run_chat(calc_repo, ["/runs", "/nope", "/quit", "ignored"], {})
    assert "No runs yet." in text
    assert "Unknown command /nope" in text


def test_transcript_keeps_raw_text(calc_repo):
    run_chat(calc_repo, ["add  *subtract*", "n"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    info = resolve_repo(calc_repo)
    [session_dir] = (ProjectPaths(info.slug).project_dir / "chats").iterdir()
    lines = (session_dir / "transcript.jsonl").read_text()
    assert '"text": "add  *subtract*"' in lines
    assert '"kind": "goal"' in lines and '"stage": "approval"' in lines
