import pytest
from pydantic import ValidationError

from phil.contracts import ALL_CONTRACTS, Approach, Approaches, ArchitectInput, DesignInput, Goal, Question
from tests.test_contract_descriptions import missing_descriptions

APPROACH = Approach(name="Footer band", summary="A full-width band above the footer.", tradeoffs=["On every page"])


def test_a_plain_string_question_is_free_text():
    goal = Goal(objective="Add a CTA section", open_questions=["Which page?"])
    assert goal.open_questions == [Question(text="Which page?", options=[])]


def test_a_saved_goal_with_string_questions_still_loads_and_round_trips():
    goal = Goal.model_validate({"objective": "Add a CTA section", "open_questions": ["Which page?"]})
    assert Goal.model_validate(goal.model_dump(mode="json")) == goal


def test_options_are_none_or_two_to_four():
    assert Question(text="q", options=["only one"]).options == []
    assert Question(text="q", options=["a", "  ", "b"]).options == ["a", "b"]
    assert Question(text="q", options=list("abcdef")).options == list("abcd")
    assert Question(text="q").options == []


def test_goal_approach_open_defaults_false():
    assert Goal(objective="Add a CTA section").approach_open is False


def test_approaches_need_two_keep_three_and_a_valid_recommendation():
    with pytest.raises(ValidationError):
        Approaches(options=[APPROACH])
    out = Approaches(options=[APPROACH] * 4, recommended=7)
    assert len(out.options) == 3 and out.recommended == 0
    assert Approaches(options=[APPROACH] * 2, recommended=1).recommended == 1


def test_new_parts_describe_every_field():
    assert missing_descriptions(Question) == []
    assert missing_descriptions(Approaches) == []
    props = ArchitectInput.model_json_schema()["properties"]
    assert "description" in props["chosen_approach"] and "description" in props["approach_note"]
    goal_props = Goal.model_json_schema()["properties"]
    assert "description" in goal_props["open_questions"] and "description" in goal_props["approach_open"]


def test_design_contracts_are_registered():
    assert Approaches in ALL_CONTRACTS and DesignInput in ALL_CONTRACTS
    assert DesignInput(goal=Goal(objective="Add a CTA section")).repo_overview == ""
