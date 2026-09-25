import pytest

from phil.agents.registry import SPECS, get_spec
from phil.agents.spec import load_prompt
from phil.config import CHAT_ROLES, RUN_ROLES
from phil.contracts import Plan, PlanCritique, Review, TaskResult, TesterReport


def test_registry_covers_chat_and_run_roles():
    # Check registered agent names
    assert set(SPECS) == {"intake", "architect", "critic", "implementer", "tester", "reviewer"}
    # Check that agents reference the correct roles
    agent_roles = {spec.role for spec in SPECS.values()}
    assert agent_roles == set(CHAT_ROLES) | set(RUN_ROLES)


@pytest.mark.parametrize(
    ("name", "out_contract"),
    [
        ("architect", Plan),
        ("critic", PlanCritique),
        ("implementer", TaskResult),
        ("tester", TesterReport),
        ("reviewer", Review),
    ],
)
def test_output_contracts(name, out_contract):
    assert get_spec(name).out_contract is out_contract


def test_only_implementer_and_tester_write_and_run_commands():
    writers = {name for name, spec in SPECS.items() if spec.writes_files}
    shell_users = {name for name, spec in SPECS.items() if "shell" in spec.tools}
    assert writers == shell_users == {"implementer", "tester"}


@pytest.mark.parametrize("name", ["architect", "critic", "implementer", "tester", "reviewer"])
def test_prompts_load_with_shared_block(name):
    prompt = load_prompt(get_spec(name))
    assert prompt.startswith("# ")
    assert "## Self-check (required)" in prompt


def test_intake_prompt_loads():
    # lean agent, so no self-check requirement
    prompt = load_prompt(get_spec("intake"))
    assert prompt.startswith("# ")
    assert "# Role: Intake" in prompt


def test_unknown_spec_raises():
    with pytest.raises(KeyError):
        get_spec("wizard")


def test_only_judging_and_intake_roles_use_the_lean_harness():
    lean = {name for name, spec in SPECS.items() if spec.harness == "lean"}
    assert lean == {"intake", "critic", "reviewer"}
    assert all(not SPECS[name].tools and not SPECS[name].writes_files for name in lean)
