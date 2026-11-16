import pytest
from pydantic import ValidationError

from phil.contracts import (
    ALL_CONTRACTS,
    ArchitectInput,
    ConverseInput,
    ConverseTurn,
    DesignInput,
    Goal,
    IntakeTurn,
    QuickTask,
)

QUICK = Goal(
    objective="Fix the README heading typo",
    depth="quick",
    task=QuickTask(id="DOCS-001", description="Fix the typo", acceptance_criteria=["heading reads Install"]),
)
FULL = Goal(objective="Add a pricing page", depth="full")
STEPS = ["Find the heading", "Fix the spelling"]


def test_a_turn_that_is_not_ready_asks_with_choices():
    turn = IntakeTurn(reply="Which page?", choices=["Home", "Pricing"], ready=False)
    assert (turn.goal, turn.steps) == (None, [])


def test_a_ready_turn_carries_a_goal_and_steps():
    turn = IntakeTurn(reply="Fixing the typo.", ready=True, goal=QUICK, steps=STEPS)
    assert turn.goal.depth == "quick"


@pytest.mark.parametrize(
    "fields",
    [
        {"ready": True, "steps": STEPS},  # no goal
        {"ready": True, "goal": QUICK},  # no steps
        {"ready": True, "goal": QUICK, "steps": ["One"]},  # too few steps
        {"ready": True, "goal": QUICK, "steps": [f"Step {n}" for n in range(8)]},  # too many
        {"ready": False, "goal": QUICK},  # a goal on a turn that isn't ready
        {"ready": False, "steps": STEPS},  # steps on a turn that isn't ready
        {"ready": True, "goal": Goal(objective="Add a pricing page", depth="answer"), "steps": STEPS},
        {"ready": True, "goal": Goal(objective="Add a pricing page"), "steps": STEPS},  # no depth
        {"ready": True, "goal": FULL.model_copy(update={"open_questions": [{"text": "Which plan?"}]}), "steps": STEPS},
        {"ready": False, "choices": ["Only one"]},
        {"ready": False, "choices": ["a", "b", "c", "d", "e"]},
        {"ready": False, "reply": "   "},
    ],
)
def test_invalid_turns_are_rejected(fields):
    with pytest.raises(ValidationError):
        IntakeTurn(**({"reply": "Okay."} | fields))


def test_blank_choices_are_dropped():
    assert IntakeTurn(reply="Which?", choices=[" a ", "", "b"], ready=False).choices == ["a", "b"]


def test_converse_input_defaults():
    data = ConverseInput(turns=[ConverseTurn(speaker="user", text="fix it")]).model_dump()
    assert data["must_decide"] is False
    assert (data["route_depth"], data["route_class"], data["detected_test_cmd"]) == (None, None, None)


def test_architect_and_designer_take_the_conversation():
    turns = [ConverseTurn(speaker="user", text="add pricing"), ConverseTurn(speaker="phil", text="Which plans?")]
    assert ArchitectInput(goal=FULL, conversation=turns).conversation == turns
    assert DesignInput(goal=FULL, conversation=turns).conversation == turns
    assert ArchitectInput(goal=FULL).conversation is None


def test_new_contracts_are_listed():
    assert ConverseInput in ALL_CONTRACTS and IntakeTurn in ALL_CONTRACTS
