from phil.agents.registry import get_spec
from phil.agents.spec import load_prompt
from phil.config import CHAT_ROLES, RUN_ROLES, ROLES
from phil.contracts import Goal, IntakeInput


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


def test_chat_and_run_roles_cover_all_roles():
    assert set(CHAT_ROLES) | set(RUN_ROLES) == set(ROLES)
