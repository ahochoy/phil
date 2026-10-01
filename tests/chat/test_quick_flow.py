"""The quick path in the chat (spec §4.1, §4.5): intake writes one task and the run starts straight away."""

import json

from phil.contracts import Task
from phil.contracts.routing import Answer
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.repo import resolve_repo
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, run_chat, transcript
from tests.chat.test_routing_flow import FIX_PROMPT, payloads, peek, route

FALLBACK = "Couldn't plan this as a quick change; planning it fully."
APPROVE = "Approve? [y / edit / n] › "
PLANNERS = {"architect": [plan()], "critic": [critique()]}


def quick_task(**kw) -> Task:
    fields = {"id": "FIX-001", "description": "Fix the typo in calc", "acceptance_criteria": ["add is spelt right"]}
    return Task(**(fields | kw))


def quick_goal(objective="Fix the typo in calc", task=None, **kw):
    return goal(objective, depth="quick", task=task or quick_task(), **kw)


def detectable(repo):
    """Give the repo a Python marker, so the quick plan's test command is detected (`pytest`)."""
    (repo / "pytest.ini").write_text("[pytest]\n")
    return repo


def run_depth(repo, run_id):
    conn = connect(ProjectPaths(resolve_repo(repo).slug).db_path)
    try:
        return get_run(conn, run_id).depth
    finally:
        conn.close()


def field(name, value):
    """How a contract field appears in an agent's packet (pretty-printed JSON inside the message)."""
    return f'"{name}": {json.dumps(value)}'


def notes(repo, kind):
    return [e for e in transcript(repo) if e["kind"] == kind]


def test_a_quick_route_with_a_task_starts_a_quick_run_without_approval(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["fix the typo in calc"], {"route": [route("simple_change")], "intake": [quick_goal()]}
    )
    assert "Simple change · quick path  (/full to plan it properly)" in text
    assert "Quick change: FIX-001 Fix the typo in calc" in text
    assert factory.remaining() == {"route": 0, "intake": 0}  # no architect or critic
    assert APPROVE not in prompts and "Plan FIX" not in text
    [(run_id, mode, decision)] = spawned
    assert (mode, decision) == ("start", None)
    [record] = runs
    assert record.run_id == run_id and record.keyword == "FIX"
    assert run_depth(calc_repo, run_id) == "quick"
    [started] = notes(calc_repo, "quick_started")
    assert (started["depth"], started["run_id"], started["task_id"]) == ("quick", run_id, "FIX-001")
    assert notes(calc_repo, "approved") == []
    [contract] = notes(calc_repo, "quick_plan")
    assert contract["contract"]["keyword"] == "FIX" and contract["contract"]["test_cmd"] == "pytest"
    assert [t["id"] for t in contract["contract"]["tasks"]] == ["FIX-001"]


def test_intake_is_told_the_route_depth_and_the_detected_test_command(calc_repo):
    detectable(calc_repo)
    _, _, _, factory, _ = run_chat(
        calc_repo, ["fix the typo in calc"], {"route": [route("simple_change")], "intake": [quick_goal()]}
    )
    [intake] = payloads(factory, "intake")
    assert field("route_depth", "quick") in intake and field("detected_test_cmd", "pytest") in intake


def test_a_quick_route_without_a_task_falls_back_to_full_planning(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["fix the typo in calc", "y"], {"route": [route("simple_change")], **FULL_SCRIPT}
    )
    assert FALLBACK in text
    assert "Quick change:" not in text
    assert "Plan CALC v1" in text and APPROVE in prompts
    assert factory.remaining() == {"route": 0, "intake": 0, "architect": 0, "critic": 0}
    [run] = runs
    assert run_depth(calc_repo, run.run_id) == "full"
    [fallback] = notes(calc_repo, "quick_fallback")
    assert fallback["reasons"] == ["intake wrote no quick task"]
    [route_note] = notes(calc_repo, "route")
    assert route_note["depth"] == "quick"  # the route stays quick; the run is full
    [approved] = notes(calc_repo, "approved")
    assert approved["depth"] == "full" and approved["run_id"] == run.run_id


def test_a_refused_check_command_falls_back_to_full_planning(calc_repo):
    detectable(calc_repo)
    task = quick_task(verify="check", check_cmd="cat /etc/hosts")  # reads outside the repo
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["fix the typo in calc", "n"],
        {"route": [route("simple_change")], "intake": [quick_goal(task=task)], **PLANNERS},
    )
    assert FALLBACK in text
    assert "Quick change:" not in text and "reads outside the repo" not in text  # not shown as an error
    assert "Plan CALC v1" in text and APPROVE in prompts
    assert spawned == [] and runs == []
    [fallback] = notes(calc_repo, "quick_fallback")
    assert any("reads outside the repo" in reason for reason in fallback["reasons"])


def test_a_quick_task_without_a_test_command_falls_back(calc_repo):
    # No phil.toml test_cmd, nothing to detect, and a tdd task: the run would have no test command.
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["fix the typo in calc", "n"], {"route": [route("simple_change")], "intake": [quick_goal()], **PLANNERS}
    )
    assert FALLBACK in text and "Plan CALC v1" in text
    assert spawned == []
    [fallback] = notes(calc_repo, "quick_fallback")
    assert any("no test command" in reason for reason in fallback["reasons"])


def test_open_questions_are_asked_before_the_quick_plan(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["fix the typo", "the one in calc.py"],
        {"route": [route("simple_change")], "intake": [goal(depth="quick", open_questions=["Which typo?"]), quick_goal()]},
    )
    assert "Which typo?" in text
    assert text.index("Which typo?") < text.index("Quick change: FIX-001")
    assert prompts[1] == "answers (or 'go' to plan anyway) › "
    assert factory.remaining() == {"route": 0, "intake": 0}
    [run] = runs
    assert run_depth(calc_repo, run.run_id) == "quick"
    second = payloads(factory, "intake")[1]
    assert "the one in calc.py" in second


def test_intake_can_choose_the_quick_path_when_the_router_defers(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["fix the typo in calc"], {"route": [route("feature", confidence=0.3)], "intake": [quick_goal()]}
    )
    assert "Not sure how big this is · intake decides" in text
    assert "Quick change: FIX-001 Fix the typo in calc" in text
    [run] = runs
    assert run_depth(calc_repo, run.run_id) == "quick"
    [intake] = payloads(factory, "intake")
    assert field("route_depth", None) in intake


def test_a_deferred_route_whose_intake_chooses_full_is_planned(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "n"],
        {"route": [route("feature", confidence=0.3)], **FULL_SCRIPT, "intake": [goal(depth="full", task=quick_task())]},
    )
    assert "Quick change:" not in text and FALLBACK not in text
    assert "Plan CALC v1" in text


def test_forced_quick_takes_the_quick_path(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(calc_repo, ["/quick fix the typo"], {"intake": [quick_goal()]})
    assert "Forced: quick path" in text
    assert "Quick change: FIX-001 Fix the typo in calc" in text
    assert factory.remaining() == {"intake": 0}
    [run] = runs
    assert run_depth(calc_repo, run.run_id) == "quick"


def test_full_at_the_fix_offer_plans_the_fix_with_the_diagnosis(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["why does calc crash?", "full", "n"],
        {"route": [route("diagnosis")], "answer": [Answer(text="The env var is unset.")], **FULL_SCRIPT},
    )
    assert FIX_PROMPT in prompts
    assert "Fix · full plan" in text
    [intake] = payloads(factory, "intake")
    assert "why does calc crash?" in intake and "Diagnosis so far:" in intake and "The env var is unset." in intake
    assert "Plan CALC v1" in text and FALLBACK not in text
    route_notes = notes(calc_repo, "route")
    assert [(n["depth"], n["source"]) for n in route_notes] == [("answer", "llm"), ("full", "fix_offer")]


def test_enter_at_the_fix_offer_takes_the_quick_path(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["why does calc crash?", ""],
        {"route": [route("diagnosis")], "answer": [Answer(text="The env var is unset.")], "intake": [quick_goal()]},
    )
    assert "Fix · quick path" in text
    assert "Quick change: FIX-001 Fix the typo in calc" in text
    [intake] = payloads(factory, "intake")
    assert "Diagnosis so far:" in intake and field("route_depth", "quick") in intake
    [run] = runs
    assert run_depth(calc_repo, run.run_id) == "quick"


def test_a_forced_ask_that_diagnoses_gets_the_fix_offer(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["/ask why does calc crash?", peek(seen)],
        {"answer": [Answer(text="The env var is unset.", diagnosis=True)]},
    )
    assert FIX_PROMPT in prompts
    assert seen["stage"] == "confirm_fix"


def test_a_forced_ask_that_is_not_a_diagnosis_gets_no_offer(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["/ask what is calc", peek(seen)], {"answer": [Answer(text="A calculator.")]}
    )
    assert FIX_PROMPT not in prompts
    assert seen["stage"] == "idle"


def test_the_full_paths_approved_run_records_full(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add subtract", "y"], {"route": [route("feature")], **FULL_SCRIPT}
    )
    [run] = runs
    assert run_depth(calc_repo, run.run_id) == "full"
    [approved] = notes(calc_repo, "approved")
    assert approved["depth"] == "full"
