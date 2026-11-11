import pytest

from phil.config import PhilConfig
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import last_error, run_chat
from tests.chat.test_conversation_flow import CONVERSATION, asks, payloads, quick_goal, ready
from tests.chat.test_quick_flow import detectable
from tests.helpers import TEST_MODELS


@pytest.fixture(autouse=True)
def _detectable(calc_repo):
    """A detectable test command (`pytest`) in the base commit, so a valid quick task makes a quick run."""
    detectable(calc_repo)


def decided(factory):
    # The packet carries the contract as `model_dump_json(indent=2)`.
    return ['"must_decide": true' in p for p in payloads(factory, "converse")]


def test_go_plans_with_what_phil_knows(calc_repo):
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "go"], {"converse": [asks(), ready(quick_goal())]}, config=CONVERSATION
    )
    assert decided(factory) == [False, True] and len(spawned) == 1


def test_the_go_command_plans_with_what_phil_knows(calc_repo):
    _, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "/go"], {"converse": [asks(), ready(quick_goal())]}, config=CONVERSATION
    )
    assert decided(factory) == [False, True]


def test_the_fourth_reply_must_decide(calc_repo):
    _, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "a", "b", "c", "d"],
        {"converse": [asks(), asks(), asks(), asks(), ready(quick_goal())]}, config=CONVERSATION,
    )
    assert decided(factory) == [False, False, False, False, True]


def test_not_ready_when_asked_to_decide_falls_back_to_classic_intake(calc_repo):
    text, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "in calc.py", "go", "y"],
        # The third turn is sent with must_decide (after "go") and still isn't ready.
        {"converse": [asks(), asks(), asks()], "intake": [goal()], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "couldn't settle on a plan" in text
    assert text.index("couldn't settle on a plan") < text.index("classic intake")
    intake = payloads(factory, "intake")[0]
    assert "add subtract" in intake and "in calc.py" in intake
    assert len(spawned) == 1


def test_a_failed_turn_falls_back_to_classic_intake(calc_repo):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"],
        {"converse": [RuntimeError("provider down")], "intake": [goal()], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    # The failure callout says what failed in plain words; the raw error is its /more 1.
    assert "provider down" in last_error(calc_repo) and "classic intake" in text
    assert payloads(factory, "intake")


def test_a_spent_budget_makes_the_next_turn_decide(calc_repo):
    # Every scripted call costs $0.01 and the router's call counts too: route + turn 1 = $0.02,
    # past the $0.015 budget, so turn 2 must decide.
    config = PhilConfig(models=TEST_MODELS, chat={"intake": "conversation", "max_cost_usd": 0.015})
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "calc.py"],
        {"converse": [asks(), ready(quick_goal())]}, config=config, usage=(100, 20, 0.01),
    )
    assert decided(factory) == [False, True]
    assert "planning budget is spent" in text


def opening(seen, text="add subtract"):
    """The chat's first message, which also keeps the controller for scripted turns to type into.
    Jobs run inline (the default `io.submit`), so a turn's scripted callable runs while the chat is
    in the `conversing` stage: input it sends is "typed while a turn is in flight"."""

    def item(controller):
        seen["controller"] = controller
        return text

    return item


def typing(seen, text, then):
    """A scripted converse turn during which the user types `text`; the turn then returns `then`."""

    def turn(scripted_turn):
        seen["controller"]._input(text)
        return then

    return turn


def test_a_message_typed_during_a_turn_joins_the_next_turn(calc_repo):
    seen = {}
    _, spawned, _, factory, _ = run_chat(
        # The reply isn't a file name: the packet's snapshot lists calc.py.
        calc_repo, [opening(seen), "keep it small"],
        {"converse": [typing(seen, "also handle negatives", asks()), asks(), ready(quick_goal())]},
        config=CONVERSATION,
    )
    second, third = payloads(factory, "converse")[1:]
    assert "also handle negatives" in second and "keep it small" not in second
    assert third.index("also handle negatives") < third.index("keep it small")
    assert len(spawned) == 1


def test_a_message_held_past_a_ready_turn_continues_the_conversation(calc_repo):
    seen = {}
    text, spawned, _, factory, _ = run_chat(
        calc_repo, [opening(seen)],
        {"converse": [typing(seen, "and multiply", ready(quick_goal())), ready(quick_goal(), reply="Adding both.")]},
        config=CONVERSATION,
    )
    assert "and multiply" in payloads(factory, "converse")[1]
    assert text.count("Plan:") == 1 and "Adding both." in text  # only the second ready turn launches
    assert len(spawned) == 1


def test_a_message_held_past_a_failed_turn_goes_to_the_classic_intake(calc_repo):
    seen = {}

    def fails(scripted_turn):
        seen["controller"]._input("and multiply")
        raise RuntimeError("provider down")

    _, _, _, factory, _ = run_chat(
        calc_repo, [opening(seen), "n"],
        {"converse": [fails], "intake": [goal()], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    intake = payloads(factory, "intake")[0]
    assert intake.index("add subtract") < intake.index("and multiply")


def test_ctrl_c_during_a_turn_cancels_the_goal(calc_repo):
    seen = {}

    def interrupted(scripted_turn):
        seen["controller"]._interrupt()
        return ready(quick_goal())  # lands after the cancel: stale, dropped

    text, spawned, _, _, _ = run_chat(
        calc_repo, [opening(seen)], {"converse": [interrupted]}, config=CONVERSATION
    )
    assert "Cancelled the current goal." in text and "Plan:" not in text and spawned == []
