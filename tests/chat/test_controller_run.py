"""The chat follows its run: progress, pauses, completion, /resume, /btw, the reopen hint, reopening.

The run watcher never runs as a thread here (see `ManualWatcher` in test_controller): script callables
change the run row / event log and then call `controller._watcher.poll_once()`, and return WAKE so the
loop drains what the watcher posted before the next prompt.
"""

import json

from phil.chat.controller import WAKE
from phil.chat.events import ChatEvent
from phil.chat.session import ChatSession
from phil.contracts import Brief
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run, update_run
from phil.store.telemetry import budget_warning_line
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, deferred, run_chat, session_dir

PAUSE_PROMPT = "CALC-001 failed 3 attempts — retry / skip / abort › "
HINT_PROMPT = "Hint for the retry (optional) › "


def _run(controller):
    paths = ProjectPaths(controller.info.slug)
    return paths, controller._run_id, connect(paths.db_path)


def set_state(controller, state, **fields):
    """Move the chat's run row to `state` (through `running` first, as a worker would)."""
    paths, run_id, conn = _run(controller)
    if get_run(conn, run_id).state == "pending":
        update_run(conn, run_id, state="running")
    update_run(conn, run_id, state=state, **fields)
    conn.close()


def escalate(summary="CALC-001 failed 3 attempts", options=("retry", "skip", "abort")):
    def step(controller):
        paths, run_id, conn = _run(controller)
        set_state(controller, "escalated", needs_attention=summary)
        run_events(paths, run_id).append("escalation", escalation={"summary": summary, "options": list(options)})
        controller._watcher.poll_once()
        return WAKE

    return step


def budget_warn(tokens=600, cost_usd=0.0, max_tokens=700, max_cost_usd=2.0, cost_source="reported"):
    def step(controller):
        paths, run_id, conn = _run(controller)
        run_events(paths, run_id).append(
            "budget_warning", tokens=tokens, cost_usd=cost_usd, max_tokens=max_tokens,
            max_cost_usd=max_cost_usd, cost_source=cost_source,
        )
        controller._watcher.poll_once()
        return WAKE

    return step


def to_state(state, **fields):
    def step(controller):
        set_state(controller, state, **fields)
        controller._watcher.poll_once()
        return WAKE

    return step


def post(kind, **data):
    def step(controller):
        controller.post(ChatEvent(kind, data))
        return WAKE

    return step


def peek(seen, key, fn):
    def step(controller):
        seen[key] = fn(controller)
        return WAKE

    return step


def test_pause_is_answered_in_the_chat(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract", "y",
            escalate(),
            peek(seen, "paused", lambda c: c.state.view().paused),
            "again", "retry", "use minus",
            peek(seen, "after", lambda c: (c.stage, c.state.view().paused)),
        ],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert (run_id, "resume", {"action": "retry", "hint": "use minus"}) in spawned
    assert f"⏸ {run_id} needs you: CALC-001 failed 3 attempts" in text
    assert "Answer one of: retry, skip, abort" in text
    assert prompts[2:8] == ["you › ", PAUSE_PROMPT, PAUSE_PROMPT, PAUSE_PROMPT, HINT_PROMPT, "you › "]
    assert seen == {"paused": True, "after": ("running", False)}


def test_pause_answer_without_a_hint(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y", escalate(), "retry", ""], FULL_SCRIPT)
    assert (runs[0].run_id, "resume", {"action": "retry"}) in spawned


def test_pause_answered_elsewhere_is_dropped(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract", "y", escalate(),
            to_state("running"),  # someone answered with `phil resume`; the worker took the row back
            peek(seen, "paused", lambda c: c.state.view().paused),
        ],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert f"{run_id} resumed." in text
    assert prompts[3:6] == [PAUSE_PROMPT, "you › ", "you › "]
    assert seen == {"paused": False}
    assert [mode for _, mode, _ in spawned] == ["start"]


def test_pause_answer_after_the_run_moved_on_spawns_nothing(calc_repo):
    def moved_on_then_answer(controller):
        set_state(controller, "running")  # not polled yet: the chat still shows the question
        return "skip"

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", "y", escalate(), moved_on_then_answer], FULL_SCRIPT
    )
    assert "The run moved on; nothing to answer." in text
    assert [mode for _, mode, _ in spawned] == ["start"]
    assert prompts[-1] == "you › "


def test_answer_command_returns_to_the_pending_question(calc_repo):
    def ctrl_c(controller):
        raise KeyboardInterrupt()

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["/answer", "add subtract", "y", escalate(), ctrl_c, "/answer", "skip"], FULL_SCRIPT
    )
    assert "Nothing needs you right now." in text
    assert prompts[4:7] == [PAUSE_PROMPT, "you › ", PAUSE_PROMPT]  # Ctrl-C backs out; /answer returns
    assert (runs[0].run_id, "resume", {"action": "skip"}) in spawned


def test_budget_warning_is_printed(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", budget_warn(), to_state("completed", tasks_done=1)], FULL_SCRIPT
    )
    run_id = runs[0].run_id
    expected = budget_warning_line(
        run_id, tokens=600, cost_usd=0.0, max_tokens=700, max_cost_usd=2.0, cost_source="reported"
    )
    assert expected in text


def test_progress_goes_to_the_toolbar(calc_repo):
    seen = {}
    before = {}

    def running(controller):
        before["printed"] = controller.console.export_text(clear=False)
        paths, run_id, conn = _run(controller)
        update_run(conn, run_id, state="running", current_node="implement")
        controller._watcher.poll_once()
        return WAKE

    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", running, peek(seen, "run", lambda c: c.state.view().run),
         peek(seen, "printed", lambda c: c.console.export_text(clear=False))],
        FULL_SCRIPT,
    )
    run = seen["run"]
    assert (run.run_id, run.keyword, run.node, run.tasks_done, run.tasks_total) == (
        runs[0].run_id, "CALC", "implement", 0, 1
    )
    assert seen["printed"] == before["printed"]  # progress never prints


def test_completion_notice_then_next_goal(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract", "y",
            to_state("completed", tasks_done=1),
            peek(seen, "view", lambda c: (c.state.view().run, c.state.view().paused, c._watcher)),
            "add multiply", "n",
        ],
        {
            "intake": [goal(), goal("Add multiply")],
            "architect": [plan(), plan(keyword="MUL")],
            "critic": [critique(), critique()],
        },
    )
    run_id = runs[0].run_id
    assert f"✓ Run {run_id} completed · 1/1 tasks · 0 tokens · $0.00" in text
    assert f"Review it: phil diff {run_id}" in text
    summary = ProjectPaths(resolve_repo(calc_repo).slug).run_dir(run_id) / "summary.md"
    assert f"Summary:{summary}" in "".join(text.split())  # the long path may wrap
    assert seen["view"] == (None, False, None)
    assert prompts[3] == "you › "
    assert "Goal: Add multiply" in text and "Plan dropped." in text
    assert "Plan MUL v2" in text  # plan versions count per chat
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    assert state["run_id"] is None and state["done_seen"] is True and state["stage"] == "idle"


def test_completion_lists_open_issues_and_aborted_runs_say_so(calc_repo):
    text, *_ = run_chat(
        calc_repo, ["add subtract", "y", to_state("completed", tasks_done=1, needs_attention="CALC-001 skipped")],
        FULL_SCRIPT,
    )
    assert "Open issues: CALC-001 skipped" in text
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y", to_state("aborted")], FULL_SCRIPT)
    aborted = [r for r in runs if r.state == "aborted"][0]
    assert f"Run {aborted.run_id} was aborted." in text


def test_failed_run_offers_resume(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "/resume",
            "add subtract", "y",
            peek(seen, "first", lambda c: c._watcher),
            to_state("failed", needs_attention="worker failed: boom"),
            peek(seen, "ended", lambda c: (c._watcher, c.stage, c._run_id)),
            "/resume",
            peek(seen, "second", lambda c: (c._watcher, c.stage)),
        ],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert "Nothing to resume." in text
    assert f"Run {run_id} failed: worker failed: boom" in text and "Continue it with /resume." in text
    assert (run_id, "continue", None) in spawned
    assert seen["first"].stopped and seen["ended"] == (None, "idle", run_id)  # kept for /resume
    watcher, stage = seen["second"]
    assert stage == "running" and watcher is not seen["first"] and watcher.started


def test_worker_lost_can_be_resumed(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", post("worker_lost"), "/resume"], FULL_SCRIPT
    )
    run_id = runs[0].run_id
    assert f"The worker for {run_id} stopped responding. Continue it with /resume." in text
    assert (run_id, "continue", None) in spawned


def test_resume_while_the_run_is_healthy_does_nothing(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y", "/resume"], FULL_SCRIPT)
    assert "Nothing to resume." in text
    assert [mode for _, mode, _ in spawned] == ["start"]


def test_watch_error_warns_and_keeps_watching(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", post("watch_error", error="OSError: disk gone"), to_state("completed", tasks_done=1)],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    flat = " ".join(text.split())
    assert f"Live updates for {run_id} are failing (OSError: disk gone); retrying. `phil attach {run_id}` also works." in flat
    assert f"✓ Run {run_id} completed" in text  # the watcher kept polling and delivered the ending


def test_btw_answers_during_planning(calc_repo):
    # `deferred()` holds every job; the plan job stays pending while the /btw job (the last one queued)
    # is run first, so its answer prints while planning is still in flight.
    submit, run_next, pending = deferred()
    seen = {}

    def run_btw(controller):
        seen["pending_before"] = controller.state.view().btw_pending
        pending.pop()()
        return WAKE

    def btw_agent(turn):
        seen["snapshot"] = (turn.workdir / "calc.py").exists()  # the tree is removed when the chat ends
        seen["payload"] = str(turn.payload)
        return Brief(headline="add lives in calc.py", points=["see def add"])

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract", run_next,  # intake done; the plan job is held
            "/btw where is add?",
            run_btw,
            peek(seen, "after", lambda c: (c.stage, c.state.view().btw_pending, len(pending))),
            run_next,  # the plan lands
            "n",
        ],
        {**FULL_SCRIPT, "btw": [btw_agent]},
        submit=submit,
    )
    assert seen["pending_before"] == 1
    assert seen["after"] == ("planning", 0, 1)
    assert "btw ›" in text and "add lives in calc.py" in text and "see def add" in text
    assert text.index("add lives in calc.py") < text.index("Plan CALC v1")
    assert "where is add?" in seen["payload"] and "Add subtract to calc" in seen["payload"]
    assert seen["snapshot"] is True
    assert prompts[:6] == ["you › "] * 6  # the planning prompt throughout
    assert "Plan dropped." in text


def test_btw_includes_run_status_and_recent_events(calc_repo):
    seen = {}

    def btw_agent(turn):
        seen["payload"] = str(turn.payload)
        return Brief(headline="it is implementing")

    text, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", escalate(), "/btw why paused?", "skip"],
        {**FULL_SCRIPT, "btw": [btw_agent]},
    )
    assert "it is implementing" in text
    assert "escalated" in seen["payload"]
    assert "escalation: CALC-001 failed 3 attempts" in seen["payload"]


def test_btw_usage_and_failure_keep_the_stage(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "/btw", "/btw hi", "y"],
        {**FULL_SCRIPT, "btw": [RuntimeError("provider down")]},
    )
    assert "Usage: /btw <question>" in text
    assert "/btw failed: RuntimeError: provider down" in text
    assert prompts[1:4] == ["Approve? [y / edit / n] › "] * 3
    assert len(runs) == 1


def test_a_failing_btw_render_keeps_the_approval_stage(calc_repo, monkeypatch):
    import phil.chat.controller as controller_mod

    def boom(console, brief, **kw):
        raise RuntimeError("render broke")

    monkeypatch.setattr(controller_mod, "render_brief", boom)
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "/btw hi", peek(seen, "btw", lambda c: c.state.view().btw_pending), "y"],
        {**FULL_SCRIPT, "btw": [Brief(headline="x")]},
    )
    assert "render broke" in text
    assert prompts[1:4] == ["Approve? [y / edit / n] › "] * 3
    assert seen["btw"] == 0 and len(runs) == 1


def test_recover_to_idle_bumps_the_generation(calc_repo, monkeypatch):
    from phil.chat.session import ChatSession as Session

    original = Session.contract

    def contract(self, kind, value):
        if kind == "goal":
            raise OSError("transcript unwritable")
        return original(self, kind, value)

    monkeypatch.setattr(Session, "contract", contract)
    submit, run_next, pending = deferred()
    seen = {}
    text, *_ = run_chat(
        calc_repo,
        ["add subtract", peek(seen, "before", lambda c: c._generation), run_next,
         peek(seen, "after", lambda c: (c._generation, c.stage))],
        FULL_SCRIPT,
        submit=submit,
    )
    assert "transcript unwritable" in text
    assert seen["after"] == (seen["before"] + 1, "idle")


def test_eof_while_running_prints_the_reopen_hint(calc_repo):
    seen = {}
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", peek(seen, "w", lambda c: c._watcher)], FULL_SCRIPT
    )
    chat_id = session_dir(calc_repo).name
    assert f"Run {runs[0].run_id} keeps working. Reopen this chat with `phil --resume {chat_id}`." in text
    assert seen["w"].started and seen["w"].stopped


def test_quit_while_paused_prints_the_reopen_hint(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y", escalate(), "/quit"], FULL_SCRIPT)
    assert f"Run {runs[0].run_id} keeps working." in text


def test_no_reopen_hint_without_a_run(calc_repo):
    text, *_ = run_chat(calc_repo, ["add subtract", "n"], FULL_SCRIPT)
    assert "keeps working" not in text


def reopen(calc_repo, answers, scripts=None):
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    session = ChatSession.open(paths, session_dir(calc_repo).name)
    return run_chat(calc_repo, answers, scripts or {}, session=session, resume=True)


def test_reopen_follows_the_run(calc_repo):
    first_text, first_spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    run_id = runs[0].run_id
    chat_id = session_dir(calc_repo).name
    seen = {}

    def progress(controller):
        seen["started"] = controller._watcher.started
        controller._watcher.poll_once()
        return WAKE

    text, spawned, runs, factory, prompts = reopen(
        calc_repo,
        [progress, peek(seen, "run", lambda c: c.state.view().run), escalate(), "abort"],
    )
    assert f"Reopened {chat_id}: Add subtract to calc" in text
    assert seen["started"] is True
    assert seen["run"].run_id == run_id and seen["run"].keyword == "CALC"
    assert prompts[:4] == ["you › ", "you › ", "you › ", PAUSE_PROMPT]
    assert spawned == [(run_id, "resume", {"action": "abort"})]


def test_reopen_at_approval_re_renders_the_plan(calc_repo):
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT)  # EOF at the approval prompt
    chat_id = session_dir(calc_repo).name
    text, spawned, runs, factory, prompts = reopen(calc_repo, ["y"])
    assert f"Reopened {chat_id}: Add subtract to calc" in text
    assert "Plan CALC v1 · 1 task" in text
    assert prompts[0] == "Approve? [y / edit / n] › "
    assert len(runs) == 1 and spawned == [(runs[0].run_id, "start", None)]
    assert runs[0].chat_id == chat_id


def test_reopen_before_a_plan_asks_for_the_goal_again(calc_repo):
    submit, run_next, pending = deferred()
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT, submit=submit)  # EOF while intake is pending
    text, spawned, runs, factory, prompts = reopen(calc_repo, ["add subtract", "n"], FULL_SCRIPT)
    assert "The chat was closed before a plan was ready; send the goal again or type a new one." in text
    assert prompts[0] == "you › " and "Plan CALC v1" in text


def test_a_revision_after_reopening_is_the_next_version(calc_repo):
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT)
    text, *_ = reopen(
        calc_repo, ["edit", "two tasks", "n"], {"architect": [plan(n=2)], "critic": [critique()]}
    )
    assert "Plan CALC v1 · 1 task" in text and "Plan CALC v2 · 2 tasks" in text


def test_resume_worker_that_exits_without_resuming_re_asks(calc_repo):
    # run_chat's spawn only records the call: like a resume worker that exits before claiming the row,
    # the row stays escalated with no worker alive or starting.
    seen = {}

    def poll(controller):
        seen["before"] = (controller.stage, controller.state.view().paused)
        controller._watcher.poll_once()
        return WAKE

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", "y", escalate(), "skip", "/answer", poll, "abort"], FULL_SCRIPT
    )
    run_id = runs[0].run_id
    assert seen["before"] == ("running", False)  # the answer was sent; nothing is asked meanwhile
    assert "Nothing needs you right now." in text  # /answer while the answer is in flight
    flat = "".join(text.split())
    log = ProjectPaths(resolve_repo(calc_repo).slug).run_dir(run_id) / "logs" / "worker.log"
    assert f"Theworkerexitedwithoutresumingtherun;see{log}" in flat
    assert prompts[-2:] == [PAUSE_PROMPT, "you › "]
    assert [(m, d) for _, m, d in spawned] == [
        ("start", None), ("resume", {"action": "skip"}), ("resume", {"action": "abort"})
    ]


def test_pause_answers_are_case_insensitive(calc_repo):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y", escalate(), "Skip"], FULL_SCRIPT)
    assert (runs[0].run_id, "resume", {"action": "skip"}) in spawned


def test_prompt_survives_an_escalation_without_a_summary(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", "y", post("run_paused", escalation={"options": ["abort"]}), "abort"], FULL_SCRIPT
    )
    assert prompts[3] == " — abort › "


def test_an_active_worker_blocks_answering_and_resume(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", escalate(), "skip", post("worker_lost"), "/resume"],
        FULL_SCRIPT,
        worker_alive=lambda record: True,
    )
    assert "The run moved on; nothing to answer." in text
    assert "Nothing to resume." in text
    assert [mode for _, mode, _ in spawned] == ["start"]


def test_resume_is_blocked_while_a_worker_is_starting(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", to_state("failed", needs_attention="boom"), "/resume"],
        FULL_SCRIPT,
        worker_starting=lambda events: True,
    )
    assert "Nothing to resume." in text
    assert [mode for _, mode, _ in spawned] == ["start"]


def test_a_failing_completion_notice_still_ends_the_run(calc_repo):
    seen = {}
    bad = {"state": "completed", "tasks_done": 1, "tasks_total": 1, "tokens": "lots", "cost_usd": 0.0,
           "needs_attention": None, "summary": "s.md"}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "y", peek(seen, "w", lambda c: c._watcher), post("run_done", **bad),
         peek(seen, "after", lambda c: (c.stage, c._run_id, c._done_seen))],
        FULL_SCRIPT,
    )
    assert "couldn't finish that" in text
    assert seen["w"].stopped and seen["after"] == ("idle", None, True)


def test_new_goal_after_a_failed_run_says_it_is_left(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", to_state("failed", needs_attention="boom"), "add multiply", "n"],
        {
            "intake": [goal(), goal("Add multiply")],
            "architect": [plan(), plan(keyword="MUL")],
            "critic": [critique(), critique()],
        },
    )
    run_id = runs[0].run_id
    assert f"Run {run_id} is left as failed; `phil resume {run_id}` still continues it." in text
    assert "Goal: Add multiply" in text


def test_run_finished_while_closed_is_shown_once_on_reopen(calc_repo):
    first_text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    run_id = runs[0].run_id
    info = resolve_repo(calc_repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    update_run(conn, run_id, state="running")
    update_run(conn, run_id, state="completed", tasks_done=1)

    def poll(controller):
        controller._watcher.poll_once()
        return WAKE

    text, *_ = reopen(calc_repo, [poll])
    assert text.count(f"✓ Run {run_id} completed") == 1
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    assert state["done_seen"] is True and state["run_id"] is None and state["stage"] == "idle"
    again, *_ = reopen(calc_repo, [])
    assert "completed" not in again and "Following run" not in again


def artifact_files(directory):
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
            and "tree" not in p.relative_to(directory).parts and p.name not in ("state.json", "transcript.jsonl")}


def test_reopen_restores_the_agent_call_counters(calc_repo):
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT)
    directory = session_dir(calc_repo)
    before = artifact_files(directory)
    state = json.loads((directory / "state.json").read_text())
    assert state["counters"] == {"intake": 1, "planner_calls": 1, "planner_version": 1, "btw": 0}
    text, *_ = reopen(
        calc_repo, ["/btw hi", "edit", "two tasks", "n"],
        {"architect": [plan(n=2)], "critic": [critique()], "btw": [Brief(headline="hello")]},
    )
    after = artifact_files(directory)
    assert {k: after[k] for k in before} == before  # nothing written before the reopen was overwritten
    new = {str(k) for k in set(after) - set(before)}
    assert any("architect-c2" in name for name in new) and any("critic-c2" in name for name in new)
    assert any("btw" in name for name in new)
    assert json.loads((directory / "state.json").read_text())["counters"]["planner_calls"] == 2


def test_reopen_during_a_revision_returns_to_approval(calc_repo):
    submit, run_next, pending = deferred()
    run_chat(
        calc_repo, ["add subtract", run_next, run_next, "edit", "two tasks"], FULL_SCRIPT, submit=submit
    )  # EOF while the revision is pending
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    assert state["stage"] == "approval" and state["plan"]["keyword"] == "CALC"
    # An older chat may have saved the revision's own stage: it reopens at approval too.
    state["stage"] = "planning"
    (session_dir(calc_repo) / "state.json").write_text(json.dumps(state))
    text, spawned, runs, factory, prompts = reopen(calc_repo, ["y"])
    assert "Plan CALC v1 · 1 task" in text
    assert prompts[0] == "Approve? [y / edit / n] › " and len(runs) == 1


# --- 4c: cost, /show, /more, /park ------------------------------------------------------------------


def test_chat_cost_reaches_the_toolbar(calc_repo):
    seen = {}
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", peek(seen, "cost", lambda c: (c.state.view().cost, c.session.id)), "n"],
        FULL_SCRIPT,
    )
    cost, chat_id = seen["cost"]
    assert cost == (0.0, "reported")
    conn = connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path)
    rows = conn.execute("SELECT DISTINCT layer, chat_id FROM telemetry").fetchall()
    assert [tuple(row) for row in rows] == [("chat", chat_id)]  # intake, architect, critic all tagged
    assert conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == 3


def test_run_cost_counts_toward_the_chat(calc_repo):
    from phil.store.telemetry import TelemetryRow, record

    seen = {}

    def run_spends(controller):
        paths, run_id, conn = _run(controller)
        record(conn, TelemetryRow(
            run_id=run_id, layer="run", node="implement", role="implementer", model="m", attempt=1,
            packet_tokens=0, input_tokens=10, output_tokens=5, latency_ms=1, cost_usd=0.25,
            outcome="ok", cost_source="estimated",
        ))
        update_run(conn, run_id, state="running", current_node="implement")
        controller._watcher.poll_once()
        return WAKE

    run_chat(
        calc_repo,
        ["add subtract", "y", run_spends, peek(seen, "cost", lambda c: c.state.view().cost)],
        FULL_SCRIPT,
    )
    assert seen["cost"] == (0.25, "estimated")


def test_park_links_the_chat_and_its_run(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["/park", "/park cache the parser", "add subtract", "y", "/park try [bold]memo[/bold]"],
        FULL_SCRIPT,
    )
    assert "Usage: /park <note>" in text
    assert "Parked P-001." in text and "Parked P-002." in text
    conn = connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path)
    rows = conn.execute("SELECT * FROM parked ORDER BY id").fetchall()
    chat_id = session_dir(calc_repo).name
    assert [(r["note"], r["run_id"], r["raised_by"], r["why_not_now"]) for r in rows] == [
        ("cache the parser", None, "user", "parked from chat"),
        ("try [bold]memo[/bold]", runs[0].run_id, "user", "parked from chat"),
    ]
    assert rows[1]["source_label"] == f"chat {chat_id}"
    assert rows[1]["source_path"] == str(session_dir(calc_repo))


def write_summary(text):
    def step(controller):
        paths, run_id, conn = _run(controller)
        (paths.run_dir(run_id) / "summary.md").write_text(text)
        return WAKE

    return step


def test_show_and_more_after_a_run(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        [
            "/show", "/more 1",
            "add subtract", "y",
            write_summary("# Run summary\n\n## Tasks\n- [x] CALC-001 [done]\n"),
            to_state("completed", tasks_done=1),
            "/more 1", "/more 9", "/more x",
            "/show",
        ],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert "No run to show yet." in text
    assert "No detail #1. Use /show to list them." in text
    assert "1 summary" in text  # the completion notice numbers its refs
    assert "# Run summary" in text and "- [x] CALC-001 [done]" in text  # /more 1 prints the file as-is
    assert "No detail #9. Use /show to list them." in text
    assert "Usage: /more <n>" in text
    assert f"Run {run_id} · CALC · completed" in text  # /show renders the chat's last run
    assert text.count("- [x] CALC-001 [done]") == 2  # /more 1 and /show's task list


REFUSED = "That detail isn't a file in the repo snapshot; not opening it."


def test_more_expands_btw_details_only_from_the_snapshot(calc_repo, tmp_path):
    from phil.contracts import Ref

    outside = tmp_path / "secret.txt"
    outside.write_text("TOP SECRET")

    def btw_agent(turn):
        (calc_repo / "calc.py").write_text("LIVE EDIT\n")  # the live tree is never read
        (turn.workdir / "link").symlink_to(outside)
        return Brief(headline="add is in calc.py", details=[
            Ref(label="calc", path="calc.py"),
            Ref(label="key", path="~/.ssh/id_rsa"),
            Ref(label="hosts", path="/etc/hosts"),
            Ref(label="up", path="../../outside"),
            Ref(label="link", path="link"),
            Ref(label="abs", path=str(outside)),
        ])

    text, *_ = run_chat(
        calc_repo,
        ["add subtract", "/btw where is add?", "/more 1", "/more 2", "/more 3", "/more 4", "/more 5", "/more 6",
         "/more 7", "n"],
        {**FULL_SCRIPT, "btw": [btw_agent]},
    )
    assert "→ 1 calc:" in text
    assert "def add(a, b):" in text and "LIVE EDIT" not in text
    assert text.count(REFUSED) == 5
    assert "TOP SECRET" not in text
    assert "No detail #7. Use /show to list them." in text


def test_btw_details_without_a_snapshot_are_refused(calc_repo):
    from phil.contracts import Ref

    brief = Brief(headline="add is in calc.py", details=[Ref(label="calc", path="calc.py")])
    text, *_ = run_chat(calc_repo, ["/btw where is add?", "/more 1"], {"btw": [brief]})
    assert REFUSED in text and "def add" not in text


def test_more_refuses_a_ref_with_an_embedded_nul_byte(calc_repo):
    # `Path.resolve()` raises ValueError ("embedded null character in path") for this, rather
    # than OSError/RuntimeError; `_inside_snapshot` must treat it as "not in the snapshot" too.
    from phil.contracts import Ref

    brief = Brief(headline="add is in calc.py", details=[Ref(label="bad", path="calc.py\x00evil")])
    text, *_ = run_chat(
        calc_repo, ["add subtract", "/btw where is add?", "/more 1", "n"], {**FULL_SCRIPT, "btw": [brief]}
    )
    assert REFUSED in text and "def add" not in text


def test_parked_count_refreshes_when_the_run_finishes(calc_repo):
    # A worker can park items (`SelfCheck.out_of_scope`) during the run; the toolbar's count,
    # last refreshed at chat/run start, must pick those up when the run ends too.
    from phil.contracts import Ref
    from phil.store.parked import park

    seen = {}

    def worker_parks(controller):
        paths, run_id, conn = _run(controller)
        park(
            conn, raised_by="implementer", note="a worker parked this", why_not_now="out of scope",
            source=Ref(label="implement output", path=""), run_id=run_id,
        )
        return WAKE

    text, spawned, runs, *_ = run_chat(
        calc_repo,
        [
            "add subtract", "y",
            peek(seen, "before", lambda c: c.state.view().parked),
            worker_parks,
            to_state("completed", tasks_done=1),
            peek(seen, "after", lambda c: c.state.view().parked),
        ],
        FULL_SCRIPT,
    )
    assert seen == {"before": 0, "after": 1}


def test_park_updates_the_parked_count(calc_repo):
    seen = {}
    run_chat(
        calc_repo,
        [peek(seen, "before", lambda c: c.state.view().parked), "/park one", "/park two",
         peek(seen, "after", lambda c: c.state.view().parked)],
        {},
    )
    assert seen == {"before": 0, "after": 2}
    seen.clear()
    run_chat(calc_repo, [peek(seen, "start", lambda c: c.state.view().parked)], {})  # counted at chat start
    assert seen == {"start": 2}


def test_help_lists_the_new_commands(calc_repo):
    text, *_ = run_chat(calc_repo, ["/help"], {})
    text = " ".join(text.split())  # the help line wraps
    assert "/show" in text and "/more <n>" in text and "/park <note>" in text
