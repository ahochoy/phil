"""Routing in the chat (spec §3.5, §3.6, §3.8): every message is answered, or planned quick or full."""

import phil.chat.controller as controller_mod
from phil.config import PhilConfig
from phil.contracts.routing import Answer, RouteJudgement
from phil.routing import Judgement
from phil.routing.classify import Classification
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, deferred, run_chat, transcript
from tests.helpers import TEST_MODEL, TEST_MODELS

FIX_PROMPT = "Fix it? [Enter = quick fix / full = plan it / n] › "
QUICK_FALLBACK = "Couldn't plan this as a quick change; planning it fully."


def route(task_class, confidence=0.9, needs_detail=0.1) -> RouteJudgement:
    return RouteJudgement(task_class=task_class, confidence=confidence, needs_detail=needs_detail)


def peek(seen):
    """A script item that records the controller's stage (and recent turns), then ends the chat."""

    def look(controller):
        seen["stage"] = controller.stage
        seen["recent"] = list(controller._recent)
        return None

    return look


def payloads(factory, role):
    return [str(payload) for name, payload in factory.calls if name == role]


def test_question_is_answered_without_intake_or_planning(calc_repo):
    seen = {}
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["what does calc do?", peek(seen)],
        {"route": [route("question")], "answer": [Answer(text="It sums.", files=["calc.py"])], **FULL_SCRIPT},
    )
    assert "Question · answering (/full to plan a change instead)" in text
    assert "It sums." in text and "Files: calc.py" in text
    assert seen["stage"] == "idle"
    assert seen["recent"] == ["you: what does calc do?", "phil: It sums."]
    assert factory.remaining() == {"route": 0, "answer": 0, "intake": 1, "architect": 1, "critic": 1}
    assert spawned == [] and runs == []


def test_simple_change_takes_the_quick_path_and_falls_back_without_a_task(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["fix the typo in calc", "n"], {"route": [route("simple_change")], **FULL_SCRIPT}
    )
    assert "Simple change · quick path  (/full to plan it properly)" in text
    assert QUICK_FALLBACK in text  # FULL_SCRIPT's intake goal has no task
    assert "Plan CALC v1" in text and "Approve? [y / edit / n] › " in prompts
    assert factory.remaining() == {"route": 0, "intake": 0, "architect": 0, "critic": 0}


def test_a_feature_is_a_full_plan_and_approval_is_remembered(calc_repo):
    seen = {}
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["add subtract", "y", peek(seen)], {"route": [route("feature")], **FULL_SCRIPT}
    )
    assert "Feature · full plan" in text
    assert seen["recent"] == ["you: add subtract", "phil: planned CALC"]
    assert len(runs) == 1


def test_low_confidence_lets_intake_decide_and_intake_can_choose_answer(calc_repo):
    seen = {}
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["why is calc slow?", peek(seen)],
        {
            "route": [route("feature", confidence=0.3)],
            "intake": [goal("Explain why calc is slow", depth="answer")],
            "answer": [Answer(text="It recomputes every call.")],
            "architect": [plan()],
            "critic": [critique()],
        },
    )
    assert "Not sure how big this is · intake decides" in text
    assert "It recomputes every call." in text
    assert seen["stage"] == "idle"
    assert factory.remaining() == {"route": 0, "intake": 0, "answer": 0, "architect": 1, "critic": 1}
    assert payloads(factory, "answer") and "why is calc slow?" in payloads(factory, "answer")[0]


def test_needs_detail_asks_first(calc_repo):
    seen = {}
    text, *_ = run_chat(
        calc_repo,
        ["fix the page", peek(seen)],
        {"route": [route("simple_change", needs_detail=0.8)], "intake": [goal(open_questions=["Which page?"])]},
    )
    assert "Unclear request · asking first" in text
    assert "Which page?" in text
    assert seen["stage"] == "questions"


def test_unavailable_router_lets_intake_decide(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"], {"route": [RuntimeError("down"), RuntimeError("down")], **FULL_SCRIPT}
    )
    assert "Not sure how big this is · intake decides" in text
    assert "Plan CALC v1" in text  # intake left the depth open: the goal is planned
    [note] = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert (note["depth"], note["source"], note["reason"], note["task_class"]) == (None, "intake", "unavailable", None)


def test_forced_prefix_skips_the_router(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["/full add auth", "n"], {"route": [route("question")], **FULL_SCRIPT}
    )
    assert "Forced: full path" in text
    assert "Plan CALC v1" in text
    assert factory.remaining()["route"] == 1
    [intake] = payloads(factory, "intake")
    assert "add auth" in intake and "/full" not in intake
    users = [(e["stage"], e["text"]) for e in transcript(calc_repo) if e["kind"] == "user"]
    assert users[0] == ("goal", "/full add auth")
    [note] = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert (note["depth"], note["source"], note["reason"]) == ("full", "forced", "forced")
    assert note["task_class"] is None and note["confidence"] is None and note["latency_ms"] is None


def test_forced_quick_says_it_is_the_quick_path(calc_repo):
    text, *_ = run_chat(calc_repo, ["/quick fix the typo", "n"], FULL_SCRIPT)
    assert "Forced: quick path" in text and "Forced: quick · planning" not in text
    assert QUICK_FALLBACK in text and "Plan CALC v1" in text
    [note] = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert (note["depth"], note["source"]) == ("quick", "forced")


def test_forced_ask_answers_directly(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["/ASK what is calc"], {"answer": [Answer(text="A calculator.")], **FULL_SCRIPT}
    )
    assert "Forced: answer path" in text and "A calculator." in text
    assert "answering (/full" not in text  # one status line per message
    assert factory.remaining()["intake"] == 1


def test_bare_override_prints_usage(calc_repo):
    seen = {}
    text, spawned, runs, factory, _ = run_chat(calc_repo, ["/ask", peek(seen)], FULL_SCRIPT)
    assert "Usage: /ask|/quick|/full|/full! <message>" in text
    assert seen["stage"] == "idle"
    assert factory.calls == []


def test_diagnosis_offers_a_fix_and_enter_plans_it(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["why does calc crash?", "", "n"],
        {
            "route": [route("diagnosis")],
            "answer": [Answer(text="The env var is unset.", files=["calc.py"])],
            **FULL_SCRIPT,
        },
    )
    assert "Diagnosis · answering" in text and "The env var is unset." in text
    assert FIX_PROMPT in prompts
    assert "Fix · quick path" in text and "Forced:" not in text
    assert QUICK_FALLBACK in text  # FULL_SCRIPT's intake goal has no task
    [intake] = payloads(factory, "intake")
    assert "why does calc crash?" in intake
    assert "Diagnosis so far:" in intake and "The env var is unset." in intake
    assert "Plan CALC v1" in text
    [answer] = [e for e in transcript(calc_repo) if e["kind"] == "answer"]
    assert answer["text"] == "The env var is unset." and answer["files"] == ["calc.py"]
    assert answer["task_class"] == "diagnosis"
    users = [(e["stage"], e["text"]) for e in transcript(calc_repo) if e["kind"] == "user"]
    assert users[:2] == [("goal", "why does calc crash?"), ("confirm_fix", "")]
    notes = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert [(n["depth"], n["source"]) for n in notes] == [("answer", "llm"), ("quick", "fix_offer")]


def test_declining_the_fix_returns_to_idle(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["why does calc crash?", "n", peek(seen)],
        {"route": [route("diagnosis")], "answer": [Answer(text="The env var is unset.")], **FULL_SCRIPT},
    )
    assert seen["stage"] == "idle"
    assert prompts[-1] == "you › "
    assert factory.remaining()["intake"] == 1


def test_ctrl_c_at_the_fix_offer_returns_to_idle(calc_repo):
    def ctrl_c(controller):
        raise KeyboardInterrupt()

    seen = {}
    run_chat(
        calc_repo,
        ["why does calc crash?", ctrl_c, peek(seen)],
        {"route": [route("diagnosis")], "answer": [Answer(text="The env var is unset.")]},
    )
    assert seen["stage"] == "idle"


def test_another_message_at_the_fix_offer_is_a_new_goal(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["why does calc crash?", "add subtract", "n"],
        {
            "route": [route("diagnosis"), route("feature")],
            "answer": [Answer(text="The env var is unset.")],
            **FULL_SCRIPT,
        },
    )
    assert "Feature · full plan" in text
    [intake] = payloads(factory, "intake")
    assert "add subtract" in intake and "Diagnosis so far:" not in intake


def test_a_failed_answer_returns_to_idle(calc_repo):
    seen = {}
    text, *_ = run_chat(
        calc_repo,
        ["what does calc do?", peek(seen)],
        {"route": [route("question")], "answer": [RuntimeError("model down"), RuntimeError("model down")]},
    )
    assert "Phil couldn't answer that:" in text and "model down" in text
    assert seen["stage"] == "idle"


def test_router_unavailable_line_when_jev_fails(calc_repo, monkeypatch):
    judgement = Judgement(
        task_class="question", probabilities={"question": 1.0}, confidence=0.9, needs_detail=0.1, source="llm",
        latency_ms=5, usage=None, fallback_reason="http 429",
    )
    monkeypatch.setattr(
        controller_mod, "classify", lambda ctx, state, **kw: Classification(judgement, fallback_reason="http 429")
    )
    config = PhilConfig(models={**TEST_MODELS, "classifier": "typesafe:jev-latest"})
    text, *_ = run_chat(
        calc_repo, ["what does calc do?"], {"answer": [Answer(text="It sums.")]}, config=config
    )
    assert "Router unavailable (http 429); using your low model." in text
    assert "Question · answering" in text
    [note] = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert note["fallback_reason"] == "http 429"


def test_router_unavailable_line_when_jev_and_the_llm_fallback_both_fail(calc_repo, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)  # Jev fails: missing key
    config = PhilConfig(models={**TEST_MODELS, "low": TEST_MODEL, "classifier": "typesafe:jev-latest"})
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"], {"route": [RuntimeError("down")], **FULL_SCRIPT}, config=config
    )
    assert "Router unavailable (missing key); intake decides." in text
    assert "using your low model" not in text
    assert "Not sure how big this is · intake decides" in text
    assert factory.remaining()["route"] == 0  # the low model was tried
    [note] = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert (note["source"], note["reason"], note["fallback_reason"]) == ("intake", "unavailable", "missing key")


def test_router_unavailable_line_when_jev_fails_and_there_is_no_low_model(calc_repo, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = PhilConfig(models={**TEST_MODELS, "classifier": "typesafe:jev-latest"})  # no low tier
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"], {"route": [route("question")], **FULL_SCRIPT}, config=config
    )
    assert "Router unavailable (missing key); intake decides." in text
    assert factory.remaining()["route"] == 1  # no llm fallback was tried
    [note] = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert (note["source"], note["reason"], note["fallback_reason"]) == ("intake", "unavailable", "missing key")


def test_route_note_is_recorded(calc_repo):
    run_chat(
        calc_repo, ["what does calc do?"], {"route": [route("question", confidence=0.8)], "answer": [Answer(text="x")]}
    )
    [note] = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert (note["task_class"], note["depth"], note["source"], note["reason"]) == ("question", "answer", "llm", "class")
    assert note["confidence"] == 0.8 and note["needs_detail"] == 0.1
    assert isinstance(note["latency_ms"], int) and note["fallback_reason"] is None


def test_new_goal_while_routing_asks_to_replace(calc_repo):
    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract",  # goal 1: its routing job is held
            "add multiply",  # a new goal while goal 1 is routing
            "y",  # replace: goal 2's routing job is held behind goal 1's
            run_next,  # goal 1's routing runs now; its result is stale
            run_next,  # goal 2's routing
            run_next,  # goal 2's intake
            run_next,  # goal 2's plan
            "n",
        ],
        {
            "route": [route("question"), route("feature")],
            "intake": [goal("Add multiply")],
            "architect": [plan()],
            "critic": [critique()],
        },
        submit=submit,
    )
    assert prompts[:3] == ["you › ", "you › ", "Replace the current goal? [y / n] › "]
    assert "Question · answering" not in text  # goal 1's route arrived after the replace and was dropped
    assert text.count("Feature · full plan") == 1
    assert "Goal: Add multiply" in text and "Plan dropped." in text
    assert pending == []


def test_forced_message_while_routing_keeps_its_depth_when_it_replaces(calc_repo):
    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "/ask what is calc", "y", run_next, run_next],
        {"route": [route("feature")], "answer": [Answer(text="A calculator.")], **FULL_SCRIPT},
        submit=submit,
    )
    assert prompts[2] == "Replace the current goal? [y / n] › "
    assert "Forced: answer path" in text and "A calculator." in text
    assert "Feature · full plan" not in text
    assert factory.remaining()["intake"] == 1
    assert pending == []


def test_a_plain_replacement_after_a_kept_forced_one_is_routed(calc_repo):
    def ctrl_c(controller):
        raise KeyboardInterrupt()

    submit, run_next, pending = deferred()
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract",  # routing is held
            "/full add auth",  # a forced replacement...
            ctrl_c,  # ...not taken: keep the current goal
            run_next,  # goal 1 is routed; its intake is held
            "add multiply",  # a plain replacement during intake
            "y",
            run_next,  # goal 1's intake (stale)
            run_next,  # goal 2's routing
            run_next,  # goal 2's intake
            run_next,  # goal 2's plan
            "n",
        ],
        {
            "route": [route("feature"), route("feature")],
            "intake": [goal(), goal("Add multiply")],
            "architect": [plan()],
            "critic": [critique()],
        },
        submit=submit,
    )
    assert "Forced:" not in text
    assert "Goal: Add multiply" in text and pending == []
    notes = [e for e in transcript(calc_repo) if e["kind"] == "route"]
    assert [n["source"] for n in notes] == ["llm", "llm"]


def test_clarifications_reach_the_answer_when_intake_chooses_one(calc_repo):
    seen = {}
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["why does the page fail?", "the login page", peek(seen)],
        {
            "route": [route("diagnosis", needs_detail=0.8)],
            "intake": [goal(open_questions=["Which page?"]), goal("Explain the login failure", depth="answer")],
            "answer": [Answer(text="The session cookie expires.")],
        },
    )
    assert "Unclear request · asking first" in text and "The session cookie expires." in text
    [answer] = payloads(factory, "answer")
    assert "why does the page fail?" in answer and "Clarification: Which page?: the login page" in answer
    assert seen["recent"] == [
        "you: why does the page fail?", "you: the login page", "phil: The session cookie expires.",
    ]
    assert seen["stage"] == "confirm_fix"  # the router's class was a diagnosis


def test_an_intake_decided_run_records_its_depth(calc_repo):
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "y"],
        {"route": [route("feature", confidence=0.3)], **FULL_SCRIPT, "intake": [goal(depth="full")]},
    )
    [approved] = [e for e in transcript(calc_repo) if e["kind"] == "approved"]
    assert approved["depth"] == "full" and approved["run_id"] == runs[0].run_id


def test_a_forced_prefix_at_approval_asks_to_finish_first(calc_repo):
    seen = {}
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["add subtract", "/ask what is calc", peek(seen)], FULL_SCRIPT
    )
    assert "Finish the current goal first, or press Ctrl-C to cancel it." in text
    assert seen["stage"] == "approval"


def test_the_answerer_reads_a_working_tree_snapshot_not_the_live_root(calc_repo):
    planted = "sk-PLANTED-not-a-real-secret-0042"
    secret_name = ".env"
    with (calc_repo / ".gitignore").open("a") as f:
        f.write(f"{secret_name}\n")
    (calc_repo / secret_name).write_text(f"OPENROUTER_API_KEY={planted}\n")
    (calc_repo / "calc.py").write_text("def add(a, b):\n    return a - b  # wip\n")
    seen = {}

    def answer(turn):
        tree = turn.workdir
        seen["tree"] = tree
        seen["calc"] = (tree / "calc.py").read_text()
        seen["secret_exists"] = (tree / secret_name).exists()
        seen["shell"] = turn.tools["run_shell"](f"cat {secret_name}")
        return Answer(text="It subtracts now.")

    text, *_ = run_chat(calc_repo, ["/ask what does add do?"], {"answer": [answer], **FULL_SCRIPT})
    assert "It subtracts now." in text
    tree = seen["tree"]
    assert not tree.resolve().is_relative_to(calc_repo.resolve())  # never the live root
    assert tree.parent.name == "tree"  # under the chat session's snapshot directory
    assert seen["calc"] == "def add(a, b):\n    return a - b  # wip\n"  # uncommitted edits are visible
    assert seen["secret_exists"] is False
    assert planted not in seen["shell"]
    assert not tree.exists()  # removed after the answer


def test_jev_judgements_use_the_jev_detail_threshold(calc_repo, monkeypatch):
    judgement = Judgement(
        task_class="simple_change", probabilities={"simple_change": 0.9}, confidence=0.9, needs_detail=0.55,
        source="jev", latency_ms=5, usage=None,
    )
    monkeypatch.setattr(controller_mod, "classify", lambda *a, **k: Classification(judgement, None))
    config = PhilConfig(models=TEST_MODELS, routing={"jev_detail_threshold": 0.5})
    text, *_ = run_chat(calc_repo, ["fix the page", peek({})], {"intake": [goal(open_questions=["Which page?"])]}, config=config)
    assert "Unclear request · asking first" in text  # 0.55 >= Jev's 0.5, though below the default 0.6
