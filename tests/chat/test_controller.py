from phil.agents.fake import ScriptedAgentFactory
from phil.chat.controller import ChatController, ChatIO
from phil.config import PhilConfig
from phil.repo import resolve_repo
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from phil.ui.theme import make_console
from tests.chat.conftest import critique, goal, plan
from tests.helpers import TEST_MODELS, run_git


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


def test_spawn_failure_reports_resume_and_keeps_chat_alive(calc_repo):
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = ["add subtract", "y", "/help"]

    def spawn(root, run_id, mode):
        raise RuntimeError("no fork")

    io = ChatIO(ask=lambda prompt: queue.pop(0) if queue else None, spawn=spawn)
    factory = ScriptedAgentFactory({"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    ChatController(info, PhilConfig(models=TEST_MODELS), conn, console, io, factory=factory).run()
    text = console.export_text()
    runs = list_runs(conn)
    assert len(runs) == 1
    assert runs[0].run_id in text
    assert "phil resume" in text
    assert "no fork" in text
    assert "/runs" in text  # /help printed afterwards: the loop kept going


def test_keyboard_interrupt_at_idle_prompt_continues(calc_repo):
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    state = {"raised": False}

    def ask(prompt):
        if not state["raised"]:
            state["raised"] = True
            raise KeyboardInterrupt()
        return "/quit"

    io = ChatIO(ask=ask, spawn=lambda *a: None)
    ChatController(info, PhilConfig(models=TEST_MODELS), conn, console, io).run()
    assert "Cancelled." in console.export_text()


def test_slash_command_failure_does_not_crash_the_repl(calc_repo, monkeypatch):
    import phil.chat.controller as controller_mod

    def boom(console, conn):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(controller_mod, "render_runs", boom)
    text, spawned, runs, _ = run_chat(calc_repo, ["/runs", "/help"], {})
    assert "couldn't finish that" in text
    assert "db exploded" in text
    assert "Commands: /runs" in text  # /help printed after recovering: the loop kept going


def test_go_answer_is_recorded_and_open_questions_noted(calc_repo):
    text, spawned, runs, factory = run_chat(
        calc_repo,
        ["add subtract", "go", "n"],
        {"intake": [goal(open_questions=["Which module?"])], "architect": [plan()], "critic": [critique()]},
    )
    assert "Planning with open questions: 1" in text
    info = resolve_repo(calc_repo)
    [session_dir] = (ProjectPaths(info.slug).project_dir / "chats").iterdir()
    lines = (session_dir / "transcript.jsonl").read_text()
    assert '"stage": "answers"' in lines and '"text": "go"' in lines


def test_base_sha_resolves_at_approval_when_not_pinned(calc_repo):
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = ["add subtract", "y"]

    def ask(prompt):
        if prompt == "Approve? [y / edit / n] › ":
            run_git(calc_repo, "commit", "--allow-empty", "-m", "more work")
        return queue.pop(0) if queue else None

    io = ChatIO(ask=ask, spawn=lambda *a: None)
    factory = ScriptedAgentFactory({"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    ChatController(info, PhilConfig(models=TEST_MODELS), conn, console, io, factory=factory).run()
    new_sha = run_git(calc_repo, "rev-parse", "HEAD").strip()
    runs = list_runs(conn)
    assert new_sha != info.head_sha
    assert runs[0].base_sha == new_sha


def test_base_sha_stays_fixed_when_explicit(calc_repo):
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = ["add subtract", "y"]

    def ask(prompt):
        if prompt == "Approve? [y / edit / n] › ":
            run_git(calc_repo, "commit", "--allow-empty", "-m", "more work")
        return queue.pop(0) if queue else None

    io = ChatIO(ask=ask, spawn=lambda *a: None)
    factory = ScriptedAgentFactory({"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    ChatController(
        info, PhilConfig(models=TEST_MODELS), conn, console, io, factory=factory, base_sha=info.head_sha
    ).run()
    runs = list_runs(conn)
    assert runs[0].base_sha == info.head_sha


def test_run_plan_json_uses_effective_test_cmd(calc_repo):
    info = resolve_repo(calc_repo)
    config = PhilConfig.model_validate({"models": TEST_MODELS, "project": {"test_cmd": "uv run pytest -q"}})
    text, spawned, runs, _ = run_chat(
        calc_repo,
        ["add subtract", "y"],
        {"intake": [goal()], "architect": [plan(test_cmd=None)], "critic": [critique()]},
        config=config,
    )
    stored = ArtifactStore(ProjectPaths(info.slug).run_dir(runs[0].run_id)).read_plan()
    assert stored.test_cmd == "uv run pytest -q"
