"""The chat's callouts (spec 2026-10-07 callouts): every decision stage is a menu answered through
`ChatIO.choose`, with a settled line once it's answered; Esc folds it and /answer reopens it; an
answer given elsewhere closes it; failures are plain-language callouts with the raw error behind /more 1.

Driven through `run_chat` (test_controller's harness) with `choose=True`: the fake ChatIO's `choose`
takes its answers from the same script as `ask`, and `calls` records which one was called, in order.
"""

import functools
from pathlib import Path

import pytest

from phil.chat.controller import OTHER_PROMPT, TYPE, WAKE, ChatController, ChatIO
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
from phil.store.db import connect
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.ui.theme import make_console
from tests.chat.conftest import ChatFactory, critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, run_chat, transcript
from tests.chat.test_controller_run import _run, escalate, post, set_state, to_state
from tests.helpers import TEST_MODELS

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


@pytest.fixture
def controller_following_a_run(calc_repo):
    return functools.partial(run_chat, calc_repo)


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


def test_a_crashed_run_shows_its_failure_callout(controller_following_a_run):
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

    text, spawned, runs, *_ = controller_following_a_run(["add subtract", "y", crash, refs], FULL_SCRIPT)
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
