"""Reopening a chat: a corrupt state.json, and the chat's explicit base."""

import json

from phil.chat.session import ChatSession
from phil.repo import resolve_repo
from phil.store.paths import ProjectPaths
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, run_chat, session_dir
from tests.chat.test_design_flow import APPROACHES, OPEN, PLANNING
from tests.chat.test_routing_flow import payloads
from tests.helpers import run_git

RESTORE_WARNING = "This chat's saved state couldn't be fully restored; starting fresh."


def reopen(calc_repo, answers, scripts=None, **kw):
    paths = ProjectPaths(resolve_repo(calc_repo).slug)
    session = ChatSession.open(paths, session_dir(calc_repo).name)
    return run_chat(calc_repo, answers, scripts or {}, session=session, resume=True, **kw)


def test_a_corrupt_state_starts_fresh_and_keeps_valid_counters(calc_repo):
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT)  # EOF at approval
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    state["plan"] = {"keyword": 7}  # schema-invalid
    state["counters"] = {"intake": "x", "planner_calls": 1, "planner_version": 1, "btw": "x"}
    path.write_text(json.dumps(state))
    seen = {}

    def peek(controller):
        seen["stage"], seen["draft"] = controller.stage, controller._draft
        seen["counters"] = (controller._intake_calls, controller.planner.counters())
        return "add subtract"

    text, spawned, runs, factory, prompts = reopen(calc_repo, [peek, "n"], FULL_SCRIPT)
    assert RESTORE_WARNING in " ".join(text.split())
    assert seen == {"stage": "idle", "draft": None, "counters": (0, (1, 1))}
    assert prompts[0] == "you › " and "Plan CALC v2" in text  # the planner kept numbering


def test_a_state_that_is_not_an_object_starts_fresh(calc_repo):
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT)
    (session_dir(calc_repo) / "state.json").write_text("[1, 2]")
    text, spawned, runs, factory, prompts = reopen(calc_repo, [])
    assert RESTORE_WARNING in " ".join(text.split())
    assert prompts == ["you › "]


def test_a_bad_goal_starts_fresh(calc_repo):
    run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    state["goal"] = {"objective": 3}
    path.write_text(json.dumps(state))
    text, spawned, runs, factory, prompts = reopen(calc_repo, [])
    assert RESTORE_WARNING in " ".join(text.split())
    assert "Following run" not in text


def test_the_explicit_base_is_saved_and_restored(calc_repo):
    info = resolve_repo(calc_repo)
    pinned = info.head_sha
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT, base_sha=pinned)  # EOF at approval
    assert json.loads((session_dir(calc_repo) / "state.json").read_text())["explicit_base_sha"] == pinned
    run_git(calc_repo, "commit", "--allow-empty", "-m", "more work")
    text, spawned, runs, *_ = reopen(calc_repo, ["y"])  # reopened without --base
    assert runs[0].base_sha == pinned
    assert "--base" not in text


def test_a_different_base_on_reopen_is_ignored_with_a_note(calc_repo):
    pinned = resolve_repo(calc_repo).head_sha
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT, base_sha=pinned)
    run_git(calc_repo, "commit", "--allow-empty", "-m", "more work")
    newer = run_git(calc_repo, "rev-parse", "HEAD").strip()
    text, spawned, runs, *_ = reopen(calc_repo, ["y"], base_sha=newer)
    assert runs[0].base_sha == pinned
    flat = " ".join(text.split())
    assert f"This chat keeps its base {pinned[:7]}; --base {newer[:7]} is ignored." in flat


def test_a_chat_that_followed_head_keeps_following_it(calc_repo):
    run_chat(calc_repo, ["add subtract"], FULL_SCRIPT)
    assert json.loads((session_dir(calc_repo) / "state.json").read_text())["explicit_base_sha"] is None
    pinned = resolve_repo(calc_repo).head_sha
    run_git(calc_repo, "commit", "--allow-empty", "-m", "more work")
    head = run_git(calc_repo, "rev-parse", "HEAD").strip()
    text, spawned, runs, *_ = reopen(calc_repo, ["y"], base_sha=pinned)
    assert runs[0].base_sha == head
    assert f"This chat keeps its base HEAD; --base {pinned[:7]} is ignored." in " ".join(text.split())


def test_ctrl_c_at_the_questions_stage_cancels_the_goal(calc_repo):
    def ctrl_c(controller):
        raise KeyboardInterrupt()

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", ctrl_c],
        {"intake": [goal(open_questions=["Which module?"])]},
    )
    assert "Cancelled the current goal." in text
    assert prompts == ["you › ", "answers (or 'go' to plan anyway) › ", "you › "]
    state = json.loads((session_dir(calc_repo) / "state.json").read_text())
    assert state["stage"] == "idle" and state["goal"] is None


def test_the_chosen_approach_survives_a_reopened_chat(calc_repo):
    run_chat(
        calc_repo, ["add a CTA section", "2"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )  # EOF at approval
    text, spawned, runs, factory, prompts = reopen(
        calc_repo, ["edit", "make it two tasks", "n"], {"architect": [plan(n=2)], "critic": [critique()]}
    )
    [architect] = payloads(factory, "architect")
    assert "A full-width band above the footer." in architect


def test_a_bad_approach_falls_back_without_spoiling_the_rest_of_the_state(calc_repo):
    run_chat(calc_repo, ["add a CTA section", "2"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING})
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    state["approach"] = {"name": 7}  # schema-invalid
    path.write_text(json.dumps(state))
    text, spawned, runs, factory, prompts = reopen(
        calc_repo, ["edit", "make it two tasks", "n"], {"architect": [plan(n=2)], "critic": [critique()]}
    )
    assert RESTORE_WARNING not in " ".join(text.split())  # a bad approach alone doesn't start fresh
    [architect] = payloads(factory, "architect")
    assert "A full-width band above the footer." not in architect  # fell back to no chosen approach


def test_an_older_states_missing_approach_restores_as_none(calc_repo):
    run_chat(calc_repo, ["add a CTA section", "2"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING})
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    del state["approach"]
    del state["approach_note"]
    path.write_text(json.dumps(state))
    text, spawned, runs, factory, prompts = reopen(
        calc_repo, ["edit", "make it two tasks", "n"], {"architect": [plan(n=2)], "critic": [critique()]}
    )
    assert RESTORE_WARNING not in " ".join(text.split())
    [architect] = payloads(factory, "architect")
    assert "A full-width band above the footer." not in architect


def test_state_and_transcript_write_failures_warn_once(calc_repo, monkeypatch):
    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ChatSession, "save_state", boom)
    monkeypatch.setattr(ChatSession, "note", boom)
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y"], FULL_SCRIPT)
    assert len(runs) == 1
    assert text.count("Couldn't save the chat state: OSError: disk full") == 1
