import json

from phil.agents.fake import ScriptedAgentFactory
from phil.chat.controller import WAKE, ChatController, ChatIO
from phil.config import PhilConfig
from phil.repo import resolve_repo
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from phil.ui.theme import make_console
from tests.chat.conftest import critique, goal, plan
from tests.helpers import MODELS_TOML, TEST_MODELS, run_git


def run_chat(repo, answers, scripts, config=None, submit=None, wake=None, **kw):
    """Drive a chat. Script items are strings, None (EOF), or callables `(controller) -> str | None | WAKE`."""
    info = resolve_repo(repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = list(answers)
    spawned = []
    holder = {}

    def ask(prompt):
        holder.setdefault("prompts", []).append(prompt)
        if not queue:
            return None
        item = queue.pop(0)
        return item(holder["controller"]) if callable(item) else item

    io = ChatIO(ask=ask, spawn=lambda root, run_id, mode, decision=None: spawned.append((run_id, mode, decision)))
    if submit is not None:
        io.submit = submit
    if wake is not None:
        io.wake = lambda: wake(holder["controller"])
    factory = ScriptedAgentFactory(scripts)
    config = config or PhilConfig(models=TEST_MODELS)
    controller = ChatController(info, config, conn, console, io, factory=factory, **kw)
    holder["controller"] = controller
    controller.run()
    return console.export_text(), spawned, list_runs(conn), factory, holder["prompts"]


def test_goal_to_approved_run(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["add subtract", "y"],
        {"intake": [goal()], "architect": [plan()], "critic": [critique(notes=["looks fine"])]},
    )
    assert [r.run_id for r in runs] == [spawned[0][0]]
    assert spawned[0][1:] == ("start", None)
    assert runs[0].keyword == "CALC"
    assert "Plan CALC v1" in text and "looks fine" in text
    assert f"phil attach {spawned[0][0]}" in text
    assert factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_clarifying_questions_are_asked_once_answered(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["add subtract", "in calc.py", "n"],
        {"intake": [goal(open_questions=["Which module?"]), goal()], "architect": [plan()], "critic": [critique()]},
    )
    assert "Which module?" in text
    assert "Plan dropped." in text
    assert spawned == [] and runs == []


def test_edit_revises_the_plan(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["add subtract", "edit", "make it two tasks", "y"],
        {"intake": [goal()], "architect": [plan(), plan(n=2)], "critic": [critique(), critique()]},
    )
    assert "Plan CALC v2 · 2 tasks" in text
    assert runs[0].tasks_total == 2


CHAT_ONLY_TOML = "[models]\n" + "".join(f'{r} = "test:model"\n' for r in ("orchestrator", "architect", "critic"))


def test_missing_run_models_block_approval(calc_repo):
    (calc_repo / "phil.toml").write_text(CHAT_ONLY_TOML)
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", "n"],
        {"intake": [goal()], "architect": [plan()], "critic": [critique()]},
    )
    assert "implementer, tester, reviewer" in text
    assert "[models]" in text
    assert spawned == [] and runs == []


def test_approval_rereads_phil_toml(calc_repo):
    (calc_repo / "phil.toml").write_text(CHAT_ONLY_TOML)
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = ["add subtract", "y", "y"]
    seen = {"approval": 0}

    def ask(prompt):
        if prompt == "Approve? [y / edit / n] › ":
            seen["approval"] += 1
            if seen["approval"] == 2:
                (calc_repo / "phil.toml").write_text(MODELS_TOML)
        return queue.pop(0) if queue else None

    spawned = []
    io = ChatIO(ask=ask, spawn=lambda root, run_id, mode, decision=None: spawned.append(run_id))
    factory = ScriptedAgentFactory({"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    ChatController(info, PhilConfig(models=TEST_MODELS), conn, console, io, factory=factory).run()
    text = console.export_text()
    assert "implementer, tester, reviewer" in text  # first y refused
    runs = list_runs(conn)
    assert [r.run_id for r in runs] == spawned and len(runs) == 1


def test_approval_reports_a_broken_phil_toml(calc_repo):
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    console = make_console(record=True, width=120)
    queue = ["add subtract", "y", "n"]

    def ask(prompt):
        if prompt == "Approve? [y / edit / n] › ":
            (calc_repo / "phil.toml").write_text("[models\n")
        return queue.pop(0) if queue else None

    io = ChatIO(ask=ask, spawn=lambda *a: None)
    factory = ScriptedAgentFactory({"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    ChatController(info, PhilConfig(models=TEST_MODELS), conn, console, io, factory=factory).run()
    assert "phil.toml" in console.export_text()
    assert list_runs(conn) == []


def test_bad_test_command_blocks_approval(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", "n"],
        {"intake": [goal()], "architect": [plan(test_cmd="pytest; rm -rf /")], "critic": [critique()]},
    )
    assert "shell operators" in text
    assert spawned == [] and runs == []


def test_agent_failure_returns_to_the_prompt(calc_repo):
    text, spawned, runs, *_ = run_chat(
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

    def spawn(root, run_id, mode, decision=None):
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
    text, spawned, runs, *_ = run_chat(calc_repo, ["/runs", "/help"], {})
    assert "couldn't finish that" in text
    assert "db exploded" in text
    assert "Commands: /runs" in text  # /help printed after recovering: the loop kept going


def test_go_answer_is_recorded_and_open_questions_noted(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
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
    (calc_repo / "phil.toml").write_text(MODELS_TOML + '[project]\ntest_cmd = "uv run pytest -q"\n')
    # The chat started with a config lacking test_cmd, so the first y re-renders the plan with phil.toml's.
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", "y"],
        {"intake": [goal()], "architect": [plan(test_cmd=None)], "critic": [critique()]},
    )
    stored = ArtifactStore(ProjectPaths(info.slug).run_dir(runs[0].run_id)).read_plan()
    assert stored.test_cmd == "uv run pytest -q"


def session_dir(repo):
    [directory] = (ProjectPaths(resolve_repo(repo).slug).project_dir / "chats").iterdir()
    return directory


def transcript(repo):
    return [json.loads(line) for line in (session_dir(repo) / "transcript.jsonl").read_text().splitlines()]


def test_architect_reads_a_snapshot_of_the_base_commit(calc_repo):
    (calc_repo / "secret.env").write_text("TOKEN=x\n")
    seen = []

    def architect(turn):
        seen.append(turn.workdir)
        assert (turn.workdir / "calc.py").exists()
        assert not (turn.workdir / "secret.env").exists()
        return plan()

    run_chat(calc_repo, ["add subtract", "n"], {"intake": [goal()], "architect": [architect], "critic": [critique()]})
    [workdir] = seen
    assert workdir.is_relative_to(session_dir(calc_repo))
    assert workdir != calc_repo


def test_snapshot_is_removed_when_the_chat_ends(calc_repo):
    for ending in (["add subtract", "n"], ["add subtract", "n", "/quit"]):
        run_chat(calc_repo, ending, {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    chats = ProjectPaths(resolve_repo(calc_repo).slug).project_dir / "chats"
    dirs = list(chats.iterdir())
    assert len(dirs) == 2
    assert all(not (d / "tree").exists() for d in dirs)


def test_snapshot_is_removed_after_eof_mid_conversation(calc_repo):
    run_chat(calc_repo, ["add subtract"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]})
    assert not (session_dir(calc_repo) / "tree").exists()


def test_transcript_records_raw_text_before_stripping(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["/runs", "  add subtract \n", " edit ", "  ", " yes "],
        {"intake": [goal()], "architect": [plan()], "critic": [critique()]},
    )
    users = [(e["stage"], e["text"]) for e in transcript(calc_repo) if e["kind"] == "user"]
    assert users == [
        ("command", "/runs"),
        ("goal", "  add subtract \n"),
        ("approval", " edit "),
        ("edit", "  "),
        ("approval", " yes "),
    ]
    assert len(runs) == 1


def test_approved_is_noted_after_the_run_is_prepared(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}
    )
    kinds = [e["kind"] for e in transcript(calc_repo)]
    assert kinds.index("approved") < kinds.index("run_started")
    [approved] = [e for e in transcript(calc_repo) if e["kind"] == "approved"]
    assert approved["run_id"] == runs[0].run_id and approved["answer"] == "y"


def test_prepare_failure_is_noted_and_reported(calc_repo, monkeypatch):
    import phil.chat.controller as controller_mod

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(controller_mod, "prepare_run", boom)
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", "/help"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}
    )
    assert "disk full" in text
    assert "Commands: /runs" in text
    kinds = [e["kind"] for e in transcript(calc_repo)]
    assert "start_failed" in kinds and "approved" not in kinds
    assert spawned == [] and runs == []


def test_start_message_names_the_base_commit(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y"], {"intake": [goal()], "architect": [plan()], "critic": [critique()]}
    )
    sha7 = runs[0].base_sha[:7]
    assert f"Run {runs[0].run_id} started in the background from {sha7}." in text


FULL_SCRIPT = {"intake": [goal()], "architect": [plan()], "critic": [critique()]}


def deferred():
    """A submit that holds jobs until the script runs them, plus the script item that runs the next one."""
    pending = []

    def run_next(controller):
        pending.pop(0)()
        return WAKE

    return pending.append, run_next, pending


def test_prompts_follow_the_stage(calc_repo):
    *_, prompts = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    assert prompts[:2] == ["you › ", "Approve? [y / edit / n] › "]


def test_runs_are_linked_to_the_chat(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    assert runs[0].chat_id == session_dir(calc_repo).name
    assert runs[0].chat_id.startswith("c-")


def test_new_goal_while_running_is_refused(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(calc_repo, ["add subtract", "y", "add multiply"], FULL_SCRIPT)
    assert f"This chat is following run {runs[0].run_id}." in text
    assert "Use /btw to ask about it" in text
    assert len(runs) == 1 and prompts[-2:] == ["you › ", "you › "]


def test_replace_goal_during_intake(calc_repo):
    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract",  # goal 1: its intake job is held
            "add multiply",  # a new goal while goal 1 is in flight
            "y",  # replace: goal 2's intake job is held behind goal 1's
            run_next,  # goal 1's intake runs now; its result is stale
            run_next,  # goal 2's intake runs
            run_next,  # goal 2's plan job runs
            "n",
        ],
        {"intake": [goal(), goal("Add multiply")], "architect": [plan()], "critic": [critique()]},
        submit=submit,
    )
    assert prompts[:3] == ["you › ", "you › ", "Replace the current goal? [y / n] › "]
    assert "Goal: Add multiply" in text
    assert "Add subtract to calc" not in text  # goal 1's result arrived after the replace and was dropped
    assert text.count("Plan CALC v1") == 1 and "Plan dropped." in text
    assert factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}
    assert pending == []
    users = [(e["stage"], e["text"]) for e in transcript(calc_repo) if e["kind"] == "user"]
    assert users[:3] == [("goal", "add subtract"), ("goal", "add multiply"), ("confirm_replace", "y")]


def test_declining_replace_during_intake_keeps_the_current_goal(calc_repo):
    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "add multiply", "n", run_next, run_next, "n"],
        FULL_SCRIPT,
        submit=submit,
    )
    assert "Keeping the current goal." in text
    assert "Goal: Add subtract to calc" in text and "Plan CALC v1" in text
    assert prompts[3] == "you › "  # back to the goal's stage while its job is pending
    assert factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_ctrl_c_during_intake_cancels_the_goal(calc_repo):
    submit, run_next, pending = deferred()
    seen = {}

    def ctrl_c(controller):
        raise KeyboardInterrupt()

    def check_cancelling(controller):
        seen["cancelling_before"] = controller.state.view().cancelling
        return run_next(controller)

    def check_after(controller):
        seen["cancelling_after"] = controller.state.view().cancelling
        seen["stage"] = controller.stage
        return None

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", ctrl_c, check_cancelling, check_after], FULL_SCRIPT, submit=submit
    )
    assert "Cancelled the current goal." in text
    assert "Plan CALC" not in text and "Goal:" not in text
    assert prompts[2:] == ["you › ", "you › "]
    assert seen == {"cancelling_before": True, "cancelling_after": False, "stage": "idle"}
    assert factory.remaining() == {"intake": 0, "architect": 1, "critic": 1}  # the stale goal never planned


def test_ctrl_c_at_approval_edit_returns_to_approval(calc_repo):
    def ctrl_c(controller):
        raise KeyboardInterrupt()

    text, spawned, runs, factory, prompts = run_chat(calc_repo, ["add subtract", "edit", ctrl_c, "n"], FULL_SCRIPT)
    assert prompts == [
        "you › ",
        "Approve? [y / edit / n] › ",
        "What should change? › ",
        "Approve? [y / edit / n] › ",
        "you › ",
    ]
    assert "Plan dropped." in text


def test_failed_revise_keeps_the_draft(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "edit", "make it two tasks", "y"],
        {"intake": [goal()], "architect": [plan(), RuntimeError("provider down")], "critic": [critique()]},
    )
    assert "couldn't finish that" in text and "provider down" in text
    assert prompts[3] == "Approve? [y / edit / n] › "
    assert len(runs) == 1 and runs[0].tasks_total == 1


def test_worker_jobs_post_events_instead_of_printing(calc_repo):
    submit, run_next, pending = deferred()
    seen = {}

    def run_quietly(controller):
        before = controller.console.export_text(clear=False)
        pending.pop(0)()
        seen["printed"] = controller.console.export_text(clear=False)[len(before):]
        seen["queued"] = [e.kind for e in list(controller.events.queue)]
        return WAKE

    text, *_ = run_chat(calc_repo, ["add subtract", run_quietly, "/quit"], FULL_SCRIPT, submit=submit)
    assert seen == {"printed": "", "queued": ["goal_ready"]}


def test_state_json_tracks_the_stage(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    assert state["stage"] == "running" and state["run_id"] == runs[0].run_id and state["plan"]["keyword"] == "CALC"
    assert state["goal"]["objective"] == "Add subtract to calc"
    assert state["base_sha"] == runs[0].base_sha and state["version"] == 1 and state["done_seen"] is False


def test_state_json_write_failure_is_silent(calc_repo, monkeypatch):
    from phil.chat.session import ChatSession

    def boom(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(ChatSession, "save_state", boom)
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    assert len(runs) == 1 and "disk full" not in text


def ctrl_c(controller):
    raise KeyboardInterrupt()


def test_refused_approval_rerenders_and_a_changed_test_command_is_shown_before_starting(calc_repo):
    def add_test_cmd(controller):
        (calc_repo / "phil.toml").write_text(MODELS_TOML + '[project]\ntest_cmd = "make check"\n')
        return "y"

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "y", add_test_cmd, "y"],
        {"intake": [goal()], "architect": [plan(test_cmd=None)], "critic": [critique()]},
    )
    assert text.count("Tests: none") == 2  # first render, then again after the refused y
    assert "Tests: make check" in text  # the second y re-rendered instead of starting
    assert text.index("Tests: make check") < text.index("started in the background")
    assert prompts.count("Approve? [y / edit / n] › ") == 3 and len(runs) == 1
    info = resolve_repo(calc_repo)
    stored = ArtifactStore(ProjectPaths(info.slug).run_dir(runs[0].run_id)).read_plan()
    assert stored.test_cmd == "make check"


def test_the_step_is_cleared_before_a_job_posts(calc_repo):
    steps = []
    run_chat(calc_repo, ["add subtract", "n"], FULL_SCRIPT, wake=lambda c: steps.append(c.state.view().step))
    assert steps == [None, None]  # goal_ready, plan_ready


def test_each_goal_plans_from_its_own_snapshot(calc_repo):
    seen = []

    def architect(turn):
        seen.append(turn.workdir)
        return plan()

    run_chat(
        calc_repo,
        ["add subtract", "n", "add multiply", "n"],
        {"intake": [goal(), goal("Add multiply")], "architect": [architect, architect], "critic": [critique()] * 2},
    )
    first, second = seen
    assert first != second and first.parent == second.parent
    assert first.name.endswith("-g1") and second.name.endswith("-g2")


def test_a_failing_event_handler_does_not_strand_the_stage(calc_repo, monkeypatch):
    from phil.chat.session import ChatSession

    original = ChatSession.contract

    def contract(self, kind, value):
        if kind == "plan":
            raise OSError("transcript unwritable")
        return original(self, kind, value)

    monkeypatch.setattr(ChatSession, "contract", contract)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", "add multiply", "/quit"],
        {"intake": [goal(), goal("Add multiply")], "architect": [plan()] * 2, "critic": [critique()] * 2},
    )
    assert "transcript unwritable" in text
    assert prompts == ["you › ", "you › ", "you › "]  # back at idle: the next goal starts, no replace question


def test_a_failing_revise_handler_returns_to_approval(calc_repo, monkeypatch):
    from phil.chat.session import ChatSession

    original = ChatSession.contract
    plans = []

    def contract(self, kind, value):
        if kind == "plan":
            plans.append(value)
            if len(plans) == 2:
                raise OSError("transcript unwritable")
        return original(self, kind, value)

    monkeypatch.setattr(ChatSession, "contract", contract)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", "edit", "two tasks", "y"],
        {"intake": [goal()], "architect": [plan(), plan(n=2)], "critic": [critique()] * 2},
    )
    assert "transcript unwritable" in text
    assert prompts[3] == "Approve? [y / edit / n] › "
    assert len(runs) == 1 and runs[0].tasks_total == 1


def test_goal_result_held_while_confirming_replace_is_handled_after_no(calc_repo):
    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "add multiply", run_next, "n", run_next, "n"],
        FULL_SCRIPT,
        submit=submit,
    )
    replace = "Replace the current goal? [y / n] › "
    assert prompts[2:4] == [replace, replace]  # the goal result arrived but waited for the answer
    assert prompts[4] == "you › "  # after n: the held result started planning
    assert "Keeping the current goal." in text
    assert "Goal: Add subtract to calc" in text and "Plan CALC v1" in text
    assert text.index("Keeping the current goal.") < text.index("Goal: Add subtract to calc")


def test_replace_goal_during_planning(calc_repo):
    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", run_next, "add multiply", "y", run_next, run_next, run_next, "n"],
        {
            "intake": [goal(), goal("Add multiply")],
            "architect": [plan(), plan(keyword="MUL")],
            "critic": [critique(), critique()],
        },
        submit=submit,
    )
    assert "Goal: Add subtract to calc" in text  # goal 1 reached planning
    assert "Plan MUL" in text and "Plan CALC" not in text  # its draft landed stale and was dropped
    assert factory.remaining() == {"intake": 0, "architect": 0, "critic": 0} and pending == []


def test_ctrl_c_during_a_plan_job_drops_the_stale_draft(calc_repo):
    submit, run_next, pending = deferred()
    seen = {}

    def check(controller):
        seen["stage"] = controller.stage
        seen["cancelling"] = controller.state.view().cancelling
        return None

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", run_next, ctrl_c, run_next, check], FULL_SCRIPT, submit=submit
    )
    assert "Goal: Add subtract to calc" in text and "Cancelled the current goal." in text
    assert "Plan CALC" not in text
    assert factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}  # the plan job ran, stale
    assert seen == {"stage": "idle", "cancelling": False}


def test_ctrl_c_during_a_revise_returns_to_approval_with_the_old_draft(calc_repo):
    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", run_next, run_next, "edit", "make it two tasks", ctrl_c, run_next, "y"],
        {"intake": [goal()], "architect": [plan(), plan(n=2)], "critic": [critique(), critique()]},
        submit=submit,
    )
    assert "Cancelled the revision." in text
    assert text.count("Plan CALC v1 · 1 task") == 2  # re-rendered after the cancel
    assert "Plan CALC v2" not in text  # the revise landed stale
    assert prompts[6] == "Approve? [y / edit / n] › "
    assert len(runs) == 1 and runs[0].tasks_total == 1
