import pytest

from phil.agents.fake import ScriptedAgentFactory
from phil.agents.registry import get_spec
from phil.agents.spec import load_prompt
from phil.chat.planning import intake
from phil.config import CHAT_ROLES, RUN_ROLES, ROLES
from phil.contracts import Goal, IntakeInput
from tests.chat.conftest import goal


def test_intake_spec():
    spec = get_spec("intake")
    assert (spec.role, spec.in_contract, spec.out_contract, spec.harness, spec.tools) == (
        "orchestrator", IntakeInput, Goal, "lean", ()
    )
    assert "# Role: Intake" in load_prompt(spec)


def test_intake_input_defaults():
    data = IntakeInput(message="add a map").model_dump()
    assert data["previous_goal"] is None
    assert data["answers"] == []
    assert data["repo_overview"] == ""
    assert data["route_depth"] is None
    assert data["detected_test_cmd"] is None


def test_goal_task_defaults_to_none():
    assert Goal(objective="Add a map").task is None


def test_chat_and_run_roles_cover_all_roles_except_the_classifier_and_answerer():
    # Neither is a checked chat or run role: routing falls back to the low model, then to intake,
    # and the answerer falls back to the orchestrator's model.
    assert set(CHAT_ROLES) | set(RUN_ROLES) == set(ROLES) - {"classifier", "answerer"}


def test_goal_depth_is_optional_and_typed():
    assert Goal(objective="Add a map").depth is None
    assert Goal(objective="Add a map", depth="answer").depth == "answer"
    with pytest.raises(ValueError):
        Goal(objective="Add a map", depth="medium")


def test_intake_prompt_documents_depth():
    from phil.agents.spec import load_prompt
    spec = get_spec("intake")
    prompt = load_prompt(spec)
    assert "depth" in prompt


def test_intake_prompt_documents_task():
    spec = get_spec("intake")
    prompt = load_prompt(spec)
    assert "`task`" in prompt


def test_architect_prompt_documents_prior_attempt():
    spec = get_spec("architect")
    prompt = load_prompt(spec)
    assert "prior_attempt" in prompt
    # It applies to the first plan too, so it has its own section before "Revisions".
    section = prompt.index("## Prior attempt")
    assert section < prompt.index("`prior_attempt`") < prompt.index("## Revisions")


def test_answer_prompt_documents_diagnosis():
    spec = get_spec("answer")
    prompt = load_prompt(spec)
    assert "diagnosis" in prompt


def test_intake_retries_once_on_a_stub_goal_and_returns_the_real_one(chat_ctx):
    # Seen live: a flaky intake call returned a stub goal (objective "placeholder", everything
    # else empty). The contract retry must give the model a second chance, and intake must return
    # the real goal from that retry rather than the stub.
    real = goal(objective="Add subtract to calc")
    factory = ScriptedAgentFactory({"intake": [{"objective": "placeholder"}, real]})
    ctx = chat_ctx(factory)
    result = intake(ctx, "add subtract", overview="")
    assert result == real
    assert factory.remaining() == {"intake": 0}


def test_intake_prompt_asks_for_missing_content_with_options():
    prompt = load_prompt(get_spec("intake"))
    assert "## When to ask" in prompt
    for word in ("link", "copy", "placement", "visual style", "quick"):
        assert word in prompt
    assert "`options`" in prompt and "`approach_open`" in prompt
