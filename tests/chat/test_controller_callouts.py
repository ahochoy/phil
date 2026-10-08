"""The chat's callouts (spec 2026-10-07 callouts): every decision stage is a menu answered through
`ChatIO.choose`, with a settled line once it's answered; Esc folds it and /answer reopens it; an
answer given elsewhere closes it; failures are plain-language callouts with the raw error behind /more 1.

Driven through `run_chat` (test_controller's harness) with `choose=True`: the fake ChatIO's `choose`
takes its answers from the same script as `ask`, and `calls` records which one was called, in order.
"""

import functools
import json
from pathlib import Path

import pytest

from phil.chat.controller import MOVING_TO_FULL, OTHER_PROMPT, TYPE, WAKE, ChatController, ChatIO
from phil.chat.decision import (
    approach_decision,
    approval_decision,
    fix_decision,
    pause_decision,
    pr_decision,
    question_decision,
    replace_decision,
)
from phil.config import PhilConfig
from phil.contracts import Approach, Question
from phil.repo import resolve_repo
from phil.run.launch import spawn_worker
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.ui.theme import make_console
from tests.chat.conftest import ChatFactory, critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, run_chat, transcript
from tests.chat.test_controller_run import _run, escalate, post, set_state, to_state
from tests.helpers import TEST_MODELS, run_git
from tests.run.conftest import TEST_CMD
from tests.run.test_launch import worker_env
from tests.run.worker_scenarios import BUILD

QUOTA = {
    "category": "quota",
    "headline": "Your openrouter account is out of credits.",
    "retries": "Phil won't retry this.",
    "action": "Add credits, then try again.",
}


@pytest.fixture
def controller(calc_repo):
    """The chat harness, without a menu: `controller(answers, scripts, **kw)` runs a chat."""
    return functools.partial(run_chat, calc_repo)


@pytest.fixture
def controller_with_choose(calc_repo):
    """The chat harness whose fake ChatIO has `choose`."""
    return functools.partial(run_chat, calc_repo, choose=True)


def bare_controller(repo) -> ChatController:
    """A controller that never runs its loop: stages and state are set by hand."""
    info = resolve_repo(repo)
    conn = connect(ProjectPaths(info.slug).db_path)
    io = ChatIO(ask=lambda prompt: None, spawn=lambda *a: None)
    return ChatController(
        info, PhilConfig(models=TEST_MODELS), conn, make_console(record=True, width=120), io,
        factory=ChatFactory({}), start_pr_monitor=False,
    )


def answers_of(decision):
    return [option.answer for option in decision.options]


def test_decision_per_stage(calc_repo):
    """With stage forced (and the needed state set) to each of questions (a question with options),
    choose_approach, approval, confirm_pr, confirm_fix, confirm_replace and paused (escalation reason
    approval), _decision() returns a Decision whose answers are exactly those of the matching Task 1
    builder; for idle, hint, edit, running, and questions while _typing_other, it returns None."""
    c = bare_controller(calc_repo)
    question = Question(text="Which module?", options=["calc.py", "ops.py"], why="It decides the file.")
    c._pending, c._replies = [question, Question(text="Name?")], []
    approaches = [Approach(name="Small", summary="A function."), Approach(name="Big", summary="A class.")]
    c._approach_order = approaches
    c._pr_offer, c._pr_force = "r-1", True
    c._run_id = "r-1"
    c._pause = {"reason": "approval", "summary": "approve", "options": ["approve", "deny"],
                "commands": ["make lint"], "task_id": "CALC-001"}
    expected = {
        "questions": question_decision(1, 2, question.text, question.why, question.options),
        "choose_approach": approach_decision([(a.name, a.summary) for a in approaches]),
        "approval": approval_decision(),
        "confirm_pr": pr_decision("r-1", True),
        "confirm_fix": fix_decision(),
        "confirm_replace": replace_decision(),
        "paused": pause_decision("r-1", c._pause),
    }
    for stage, want in expected.items():
        c.stage = stage
        got = c._decision()
        assert got is not None, stage
        assert answers_of(got) == answers_of(want), stage
    for stage in ("idle", "hint", "edit", "running"):
        c.stage = stage
        assert c._decision() is None, stage
    c.stage, c._typing_other = "questions", True
    assert c._decision() is None


def test_the_loop_uses_choose_and_prints_the_settled_line(controller_with_choose, calc_repo):
    """A fake ChatIO whose choose returns "y" at the approval stage: the console shows
    "✓ Approve and run", and the run is launched exactly as typing "y" would."""
    calls = []
    text, spawned, runs, *_ = controller_with_choose(["add subtract", "y"], FULL_SCRIPT, calls=calls)
    assert "✓ Approve and run" in text
    [chosen] = [call for call in calls if call[0] == "choose"]
    assert chosen[2].title == "Approve this plan?"
    # As typed: the input is recorded at the approval stage, and the run is approved with "y" and spawned.
    lines = transcript(calc_repo)
    assert {"kind": "user", "text": "y", "stage": "approval"}.items() <= next(
        line for line in lines if line.get("text") == "y"
    ).items()
    approved = next(line for line in lines if line.get("kind") == "approved")
    assert approved["answer"] == "y" and approved["run_id"] == runs[0].run_id
    assert [r.run_id for r in runs] == [spawned[0][0]]
    assert spawned[0][1:] == ("start", None)


def test_type_folds_and_answer_reopens(controller_with_choose):
    """At a paused stage choose returns TYPE: the next io call is ask() (not choose), with a prompt that
    starts with "⏸ r-… needs you · /answer to choose"; typing "/answer" makes the following call choose() again."""
    calls = []
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", escalate(), TYPE, "/answer", "skip"], FULL_SCRIPT, calls=calls
    )
    run_id = runs[0].run_id
    pause_at = next(i for i, call in enumerate(calls) if call[0] == "choose" and call[2].kind == "pause")
    folded, reopened = calls[pause_at + 1], calls[pause_at + 2]
    assert folded[0] == "ask"
    assert folded[1].startswith(f"⏸ {run_id} needs you · /answer to choose")
    assert reopened[0] == "choose" and reopened[2].kind == "pause"
    assert (run_id, "resume", {"action": "skip"}) in spawned


def test_a_typed_option_goes_to_its_typed_prompt(controller_with_choose):
    """At the questions stage choose returns the "Something else" answer (n+1): the next call is ask()
    with the existing OTHER_PROMPT; its reply becomes the answer."""
    calls, seen = [], {}
    question = Question(text="Which module?", options=["calc.py", "ops.py"])

    def drop(controller):
        seen["clarifications"] = list(controller._clarifications)
        return "n"

    text, *_ = controller_with_choose(
        ["add subtract", "3", "lib.py", drop],
        {"intake": [goal(open_questions=[question]), goal()], "architect": [plan()], "critic": [critique()]},
        calls=calls,
    )
    at = next(i for i, call in enumerate(calls) if call[0] == "choose" and call[2].kind == "question")
    assert calls[at + 1] == ("ask", OTHER_PROMPT)
    assert seen["clarifications"] == ["Which module?: lib.py"]


def test_with_a_menu_the_printed_option_lists_are_left_to_the_callout(controller_with_choose):
    """With `choose`, a question prints its text but not its numbered options (without it, the list
    still prints: test_questions_flow); each pick leaves a settled line."""
    question = Question(text="Which module?", options=["calc.py", "ops.py"])
    scripts = lambda: {"intake": [goal(open_questions=[question]), goal()], "architect": [plan()],  # noqa: E731
                       "critic": [critique()]}
    menu_text, *_ = controller_with_choose(["add subtract", "1", "n"], scripts())
    assert "1. Which module?" in menu_text
    assert "1. calc.py" not in menu_text and "Something else" not in menu_text
    assert "Answer: calc.py" in menu_text and "· Cancel" in menu_text  # the settled lines


def test_answered_elsewhere_closes_the_callout(controller_with_choose):
    """While paused (callout open), a run_resumed event arrives: the console shows
    "r-… was answered elsewhere." and the next io call is ask() with the running prompt."""
    calls = []
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", escalate(), to_state("running")], FULL_SCRIPT, calls=calls
    )
    run_id = runs[0].run_id
    assert f"{run_id} was answered elsewhere." in text
    assert calls[-2][0] == "choose" and calls[-2][2].kind == "pause"
    assert calls[-1] == ("ask", "you › ")


def test_job_failure_prints_a_failure_callout_and_more_1_shows_the_raw_error(controller):
    """A job_failed event carrying failure={category: quota, headline: "Your openrouter account is out
    of credits.", retries: "Phil won't retry this.", action: "Add credits, then try again."} and
    error="OpenRouterError: 402 …": the console shows the ✗ headline, the retries line and the action,
    and NOT the text "OpenRouterError"; then "/more 1" prints the raw error text."""
    error = "OpenRouterError: 402 Payment Required"
    seen = {}

    def before_more(c):
        seen["text"] = c.console.export_text(clear=False)
        return "/more 1"

    text, *_ = controller([post("job_failed", job="goal_ready", error=error, failure=QUOTA), before_more], {})
    assert "✗ Your openrouter account is out of credits." in seen["text"]
    assert "Phil won't retry this. Add credits, then try again." in seen["text"]
    assert "OpenRouterError" not in seen["text"]
    assert error in text


def test_a_job_failure_without_failure_data_still_reports(controller):
    """A job_failed event with only error= (old shape): an internal-category callout is shown."""
    text, *_ = controller([post("job_failed", job="goal_ready", error="RuntimeError: boom"), "/more 1"], {})
    assert "✗ Something went wrong inside Phil (RuntimeError)." in text
    assert "RuntimeError: boom" in text  # behind /more 1


def test_a_crashed_run_shows_its_failure_callout(controller):
    """run_done with state failed, and the run's events.jsonl holding a failure event: the failure
    callout is printed with its headline."""
    seen = {}

    def crash(c):
        paths, run_id, conn = _run(c)
        run_events(paths, run_id).append("worker", mode="start", pid=1)
        run_events(paths, run_id).append("failure", **QUOTA)
        set_state(c, "failed", needs_attention="worker failed: OpenRouterError: 402")
        c._watcher.poll_once()
        return WAKE

    def refs(c):
        seen["refs"] = list(c._last_refs)
        return WAKE

    # The chat follows its run (the harness's ManualWatcher); `crash` makes it fail as a worker would.
    text, spawned, runs, *_ = controller(["add subtract", "y", crash, refs], FULL_SCRIPT)
    run_id = runs[0].run_id
    assert f"Run {run_id} failed." in text
    assert "✗ Your openrouter account is out of credits." in text
    assert "Phil won't retry this. Add credits, then try again." in text
    assert "Continue it with /resume." in text
    assert Path(seen["refs"][0].path).parts[-3:] == (run_id, "logs", "worker.log")  # /more 1


def test_a_capped_pause_body_is_behind_more_1(controller_with_choose):
    """A review_failed pause with 20 problems: its body is capped, and /more 1 prints all 20."""
    problems = [f"problem number {n}" for n in range(1, 21)]

    def review_failed(c):
        paths, run_id, conn = _run(c)
        set_state(c, "escalated", needs_attention="no valid review")
        run_events(paths, run_id).append("escalation", escalation={
            "reason": "review_failed", "summary": "The reviewer gave no valid review",
            "problems": problems, "options": ["retry", "abort"],
        })
        c._watcher.poll_once()
        return WAKE

    calls = []
    text, *_ = controller_with_choose(["add subtract", "y", review_failed, TYPE, "/more 1"], FULL_SCRIPT, calls=calls)
    pause = next(call[2] for call in calls if call[0] == "choose" and call[2].kind == "pause")
    assert pause.body[-1] == "… see /more 1"
    for problem in problems:
        assert problem in text


def review_failed_pause(problems):
    def step(c):
        paths, run_id, conn = _run(c)
        set_state(c, "escalated", needs_attention="no valid review")
        run_events(paths, run_id).append("escalation", escalation={
            "reason": "review_failed", "summary": "The reviewer gave no valid review",
            "problems": problems, "options": ["retry", "abort"],
        })
        c._watcher.poll_once()
        return WAKE

    return step


def test_answer_points_more_1_back_at_a_capped_decision(controller_with_choose):
    """While a capped pause is folded, /show replaces the details; /answer reopens it and /more 1
    is the decision's full body again."""
    problems = [f"problem number {n}" for n in range(1, 21)]
    seen = {}

    def refs(c):
        seen["after_show"] = [ref.label for ref in c._last_refs]
        return "/answer"

    text, *_ = controller_with_choose(
        ["add subtract", "y", review_failed_pause(problems), TYPE, "/show", refs, TYPE, "/more 1"], FULL_SCRIPT
    )
    assert "details" not in seen["after_show"]  # /show's listing replaced it
    for problem in problems:
        assert problem in text


def test_a_refused_approval_prints_no_settled_line(controller_with_choose, calc_repo):
    """Approval refused for a launch problem (a bad test command): no "✓ Approve and run"; the
    later "n" settles with "· Cancel" before its effect ("Plan dropped.")."""
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", "n"],
        {"intake": [goal()], "architect": [plan(test_cmd="pytest; rm -rf /")], "critic": [critique()]},
    )
    assert "shell operators" in text
    assert "✓ Approve and run" not in text
    assert text.index("· Cancel") < text.index("Plan dropped.")
    assert spawned == [] and runs == []


def test_an_approved_plan_settles_before_the_run_starts(controller_with_choose):
    text, spawned, runs, *_ = controller_with_choose(["add subtract", "y"], FULL_SCRIPT)
    assert text.index("✓ Approve and run") < text.index(f"Run {runs[0].run_id} started")


def test_each_answer_follows_its_question(controller_with_choose):
    """Two questions answered through choose: question 1, its Answer:, question 2, its Answer:."""
    first = Question(text="Which module?", options=["calc.py", "ops.py"], why="It decides the file.")
    second = Question(text="Which name?", options=["minus", "subtract"])
    text, *_ = controller_with_choose(
        ["add subtract", "1", "1", "n"],
        {"intake": [goal(open_questions=[first, second]), goal()], "architect": [plan()], "critic": [critique()]},
    )
    order = [text.index(s) for s in ("1. Which module?", "Answer: calc.py", "2. Which name?", "Answer: minus")]
    assert order == sorted(order), text


def interrupt(c):
    raise KeyboardInterrupt


def test_retry_settles_when_the_resume_is_sent(controller_with_choose):
    """Retry, then a hint: "✓ Retry the task" prints once the resume is sent (before "Resuming")."""
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", escalate(), "retry", "try harder"], FULL_SCRIPT
    )
    assert text.count("✓ Retry the task") == 1
    assert text.index("✓ Retry the task") < text.index(f"Resuming {runs[0].run_id} with retry.")
    assert (runs[0].run_id, "resume", {"action": "retry", "hint": "try harder"}) in spawned


def test_retry_then_ctrl_c_at_the_hint_prints_no_settled_line(controller_with_choose):
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", escalate(), "retry", interrupt, "/answer", "skip"], FULL_SCRIPT
    )
    assert "Answer it later with /answer." in text
    assert "✓ Retry the task" not in text
    assert "✓ Skip this task" in text  # the later answer still settles
    assert [mode for _, mode, _ in spawned] == ["start", "resume"]


def test_a_pause_answered_after_the_run_moved_on_prints_no_settled_line(controller_with_choose):
    def moved_on(c):
        set_state(c, "running")  # the run moved on while the callout was open
        return "skip"

    text, spawned, *_ = controller_with_choose(["add subtract", "y", escalate(), moved_on], FULL_SCRIPT)
    assert "The run moved on; nothing to answer." in text
    assert "Skip this task" not in text
    assert [mode for _, mode, _ in spawned] == ["start"]


def attempts_pause(problems, log=None):
    def step(c):
        paths, run_id, conn = _run(c)
        set_state(c, "escalated", needs_attention="CALC-001 failed 3 attempts")
        run_events(paths, run_id).append("escalation", escalation={
            "reason": "attempts", "task_id": "CALC-001", "phase": "green", "summary": "CALC-001 failed 3 attempts",
            "problems": problems, "options": ["retry", "skip", "abort"], "log": log,
        })
        c._watcher.poll_once()
        return WAKE

    return step


def test_an_attempts_pause_details_are_its_problems(controller):
    """No log: when the pause opens, /more 1 is decision.txt with the summary and every problem."""
    problems = ["first problem here", "second problem here", "third problem", "fourth problem"]
    seen = {}

    def before(c):
        seen["text"] = c.console.export_text(clear=False)
        return "/more 1"

    text, *_ = controller(["add subtract", "y", attempts_pause(problems), before], FULL_SCRIPT)
    assert "fourth problem" not in seen["text"]
    for problem in problems:
        assert problem in text
    assert "CALC-001 failed 3 attempts" in text


def test_an_attempts_pause_details_are_its_log(controller_with_choose, tmp_path):
    """With a log path, /more 1 is the log."""
    log = tmp_path / "green.log"
    log.write_text("AssertionError: subtract(3, 1) == 4\n", encoding="utf-8", newline="\n")
    calls = []
    text, *_ = controller_with_choose(
        ["add subtract", "y", attempts_pause(["the tests failed"], log=str(log)), TYPE, "/more 1"], FULL_SCRIPT,
        calls=calls,
    )
    pause = next(call[2] for call in calls if call[0] == "choose" and call[2].kind == "pause")
    assert pause.body[-1] == "Details: /more 1"
    assert "AssertionError: subtract(3, 1) == 4" in text


def test_a_render_capped_pause_body_is_behind_more_1(controller_with_choose):
    """Two long problems fit in the body's line count but not in its rendered rows: /more 1 still
    holds them."""
    problems = [" ".join([f"alpha{n}" for n in range(60)]), " ".join([f"omega{n}" for n in range(60)])]
    text, *_ = controller_with_choose(
        ["add subtract", "y", review_failed_pause(problems), TYPE, "/more 1"], FULL_SCRIPT
    )
    assert "omega59" in text


def test_answer_after_ctrl_c_and_show_points_more_1_back_at_the_pause(controller_with_choose):
    """Pause, Ctrl-C, /show, /answer, Esc, then /more 1: it shows the pause's details again."""
    problems = ["first problem here", "second problem here", "third problem", "fourth problem"]
    seen = {}

    def refs(c):
        seen["after_show"] = [ref.label for ref in c._last_refs]
        return "/answer"

    text, *_ = controller_with_choose(
        ["add subtract", "y", attempts_pause(problems), interrupt, "/show", refs, TYPE, "/more 1"], FULL_SCRIPT
    )
    assert "details" not in seen["after_show"]  # /show's listing replaced it
    assert "fourth problem" in text


def test_a_details_write_that_fails_still_opens_the_pause(controller_with_choose):
    """A problem with a lone surrogate can't be written to decision.txt: the chat still pauses,
    and its menu answers the pause."""
    seen = {}

    def stage(c):
        seen["stage"] = c.stage
        return "skip"

    calls = []
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", attempts_pause(["bad \ud800 text"]), stage], FULL_SCRIPT, calls=calls
    )
    assert seen["stage"] == "paused"
    assert any(call[0] == "choose" and call[2].kind == "pause" for call in calls)
    assert (runs[0].run_id, "resume", {"action": "skip"}) in spawned


def test_a_full_answer_after_the_run_moved_on_prints_nothing(controller_with_choose):
    def moved_on(c):
        set_state(c, "running")
        return "full"

    text, spawned, *_ = controller_with_choose(
        ["add subtract", "y", escalate(options=("retry", "full", "abort")), moved_on], FULL_SCRIPT
    )
    assert "The run moved on; nothing to answer." in text
    assert MOVING_TO_FULL not in text
    assert "Plan it fully instead" not in text
    assert [mode for _, mode, _ in spawned] == ["start"]


def test_a_full_answer_prints_moving_to_full_after_its_settled_line(controller_with_choose):
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", escalate(options=("retry", "full", "abort")), "full"], FULL_SCRIPT
    )
    assert text.index("✓ Plan it fully instead") < text.index(MOVING_TO_FULL)
    assert (runs[0].run_id, "resume", {"action": "full"}) in spawned


def test_an_approved_command_reaches_the_resumed_worker(calc_repo):
    """End to end, with real workers: the run pauses for approval of a command, the chat's choose
    returns "approve", and the resume worker it spawns runs that command (the scenario fails the
    run if the command is still denied), so the run completes."""
    (calc_repo / "tools").mkdir()
    (calc_repo / "tools" / "build.py").write_text("print('built ok')\n", encoding="utf-8", newline="\n")
    run_git(calc_repo, "add", "-A")
    run_git(calc_repo, "commit", "-m", "add build script")
    scenario = {"start": "needs_approval", "resume": "after_approval"}
    procs = []

    def spawn(root, run_id, mode, decision):
        procs.append(spawn_worker(root, run_id, mode, decision, env=worker_env(scenario[mode])))

    def wait_and_poll(c):
        assert procs[-1].wait(timeout=180) == 0
        c._watcher.poll_once()
        return WAKE

    def wait(c):
        assert procs[-1].wait(timeout=180) == 0
        return None

    calls = []
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", wait_and_poll, "approve", wait],
        {"intake": [goal()], "architect": [plan(test_cmd=TEST_CMD)], "critic": [critique()]},
        choose=True, calls=calls, spawn=spawn,
    )
    run_id = runs[0].run_id
    pause = next(call[2] for call in calls if call[0] == "choose" and call[2].kind == "approval")
    assert "  " + BUILD in pause.body
    assert spawned[1] == (run_id, "resume", {"action": "approve"})
    assert "✓ Approve for this run · " + BUILD in text
    record = get_run(connect(ProjectPaths(resolve_repo(calc_repo).slug).db_path), run_id)
    assert record.state == "completed"


def test_a_btw_failure_writes_one_note(controller, calc_repo):
    controller(["add subtract", "/btw hi", look({}, "x", lambda c: None), "n"],
               {**FULL_SCRIPT, "btw": [RuntimeError("provider down")]})
    notes = [line for line in transcript(calc_repo) if "provider down" in json.dumps(line)]
    assert [line["kind"] for line in notes] == ["btw_failed"]


def test_answered_elsewhere_while_typing_the_hint(controller_with_choose):
    """Retry was picked (the hint prompt is open), then the run is resumed from elsewhere: the chat
    says so and returns to the running prompt."""
    calls = []
    text, spawned, runs, *_ = controller_with_choose(
        ["add subtract", "y", escalate(), "retry", to_state("running")], FULL_SCRIPT, calls=calls
    )
    run_id = runs[0].run_id
    assert f"{run_id} was answered elsewhere." in text
    assert calls[-1] == ("ask", "you › ")
    assert [mode for _, mode, _ in spawned] == ["start"]


def look(seen, key, fn):
    """A script item that records `fn(controller)` under `key`, then lets the loop drain."""

    def step(c):
        seen[key] = fn(c)
        return WAKE

    return step


def test_a_btw_failure_is_a_failure_callout_and_keeps_the_btw_count(controller):
    seen = {}
    text, *_ = controller(
        ["add subtract", "/btw hi", look(seen, "pending", lambda c: c.state.view().btw_pending), "/more 1", "n"],
        {**FULL_SCRIPT, "btw": [RuntimeError("provider down")]},
    )
    assert "✗ Something went wrong inside Phil (RuntimeError)." in text
    assert "/btw failed" not in text
    assert seen["pending"] == 0
    assert text.count("provider down") == 1  # only behind /more 1


def test_an_ask_failure_is_a_failure_callout_and_returns_to_idle(controller):
    seen = {}
    text, *_ = controller(
        ["/ask what does calc do?", look(seen, "stage", lambda c: c.stage), "/more 1"],
        {"answer": [RuntimeError("model down")]},
    )
    assert "✗ Something went wrong inside Phil (RuntimeError)." in text
    assert "couldn't answer that" not in text
    assert seen["stage"] == "idle"
    assert text.count("model down") == 1  # only behind /more 1


def test_a_failed_error_write_clears_the_details(controller, calc_repo):
    """When last_error.txt can't be written, /more 1 doesn't open an older listing's first detail."""
    from phil.contracts import Ref

    def setup(c):
        (c.session.dir / "last_error.txt").mkdir()  # writing the file now fails
        c._set_refs([Ref(label="older", path=str(c.session.dir / "state.json"))])
        return WAKE

    text, *_ = controller([setup, post("job_failed", job="goal_ready", error="RuntimeError: boom"), "/more 1"], {})
    assert "✗ Something went wrong inside Phil (RuntimeError)." in text
    assert "No detail 1." in text
