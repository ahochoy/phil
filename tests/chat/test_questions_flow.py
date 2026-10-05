"""Intake's questions are asked one at a time, as multiple choice (spec §3.3)."""

from phil.contracts import Question
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
from tests.chat.test_quick_flow import detectable, quick_goal
from tests.chat.test_routing_flow import payloads, peek, route

PLACEMENT = Question(text="Where should the CTA go?", options=["Above the footer", "After the hero"], why="Changes the layout.")
LINKS = Question(text="Where do the buttons link?", options=["Existing pages", "Placeholder links for now"])
COPY = Question(text="What should the heading say?")
PLANNING = {"architect": [plan()], "critic": [critique()]}


def shown(seen, key, reply):
    """A script item that records the console so far, then replies."""

    def look(controller):
        seen[key] = controller.console.export_text(clear=False)
        return reply

    return look


def test_questions_are_asked_one_at_a_time_then_intake_gets_every_answer(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", shown(seen, "first", "1"), shown(seen, "second", "Placeholder links for now"), "Book a call", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS, COPY]), goal()], **PLANNING},
    )
    assert "1. Where should the CTA go?" in seen["first"] and "Changes the layout." in seen["first"]
    assert "1. Above the footer" in seen["first"] and "3. Something else (type it)" in seen["first"]
    assert "Where do the buttons link?" not in seen["first"]
    assert "2. Where do the buttons link?" in seen["second"] and "What should the heading say?" not in seen["second"]
    assert "3. What should the heading say?" in text
    first, second = payloads(factory, "intake")  # one follow-up call, after the last answer
    assert "Where should the CTA go?: Above the footer" in second
    assert "Where do the buttons link?: Placeholder links for now" in second
    assert "What should the heading say?: Book a call" in second


def test_something_else_asks_for_the_text(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "3", "Right under the pricing table", "2", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS]), goal()], **PLANNING},
    )
    assert "Your answer › " in prompts
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: Right under the pricing table" in second
    assert "Where do the buttons link?: Placeholder links for now" in second


def test_option_numbers_with_punctuation_pick_the_option(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "2.", "1)", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS]), goal()], **PLANNING},
    )
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: After the hero" in second
    assert "Where do the buttons link?: Existing pages" in second


def test_an_out_of_range_number_asks_again(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "9", "1", "n"],
        {"intake": [goal(open_questions=[PLACEMENT]), goal()], **PLANNING},
    )
    assert "Pick 1–3, or type your answer." in text
    assert "Where should the CTA go?: Above the footer" in payloads(factory, "intake")[1]


def test_typed_text_and_digits_at_a_free_text_question_are_the_answer(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "Somewhere visible", "42", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, COPY]), goal()], **PLANNING},
    )
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: Somewhere visible" in second
    assert "What should the heading say?: 42" in second


def test_go_after_some_answers_sends_them_and_asks_nothing_more(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "1", "go", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS]), goal(open_questions=[COPY])], **PLANNING},
    )
    first, second = payloads(factory, "intake")
    assert "Where should the CTA go?: Above the footer" in second
    assert "1. What should the heading say?" not in text  # the second round isn't asked
    assert "Planning with open questions: 1" in text


def test_go_before_any_answer_plans_straight_away(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "go", "n"], {"intake": [goal(open_questions=[PLACEMENT])], **PLANNING}
    )
    assert len(payloads(factory, "intake")) == 1
    assert "Planning with open questions: 1" in text


def test_ctrl_c_while_typing_something_else_cancels_the_goal(calc_repo):
    seen = {}

    def ctrl_c(controller):
        raise KeyboardInterrupt

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "3", ctrl_c, peek(seen)],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS])], **PLANNING},
    )
    assert "Cancelled the current goal." in text
    assert seen["stage"] == "idle"
    assert len(payloads(factory, "intake")) == 1 and spawned == []


def test_the_cta_request_is_asked_about_before_a_quick_task_is_written(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section at the bottom of the page", "1", "2"],
        {"route": [route("simple_change")], "intake": [goal(open_questions=[PLACEMENT, LINKS]), quick_goal()]},
    )
    assert text.index("Where should the CTA go?") < text.index("Where do the buttons link?") < text.index("Quick change:")
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: Above the footer" in second
    assert "Where do the buttons link?: Placeholder links for now" in second
    assert len(spawned) == 1
