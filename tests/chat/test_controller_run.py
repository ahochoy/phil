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
    assert "Goal: Add multiply" in text and "Plan MUL v2" in text  # plan versions count per chat and "Plan dropped." in text
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


def test_watch_error_points_at_attach(calc_repo):
    seen = {}
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", post("watch_error", error="OSError: disk gone"), peek(seen, "w", lambda c: c._watcher)],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert "Live updates for" in text and "OSError: disk gone" in text
    assert f"phil attach {run_id}" in text.split("Live updates for")[1]
    assert seen["w"] is None


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

    def boom(console, brief):
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
