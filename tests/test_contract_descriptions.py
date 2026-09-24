import pytest

from phil.contracts import (
    ALL_CONTRACTS,
    ArchitectInput,
    CriticInput,
    Goal,
    ImplementInput,
    Plan,
    PlanCritique,
    ReviewInput,
    Review,
    Task,
    TaskResult,
    TesterInput,
    TesterReport,
    TestReport,
)

AGENT_OUTPUTS = [Plan, PlanCritique, TaskResult, TesterReport, Review]


def missing_descriptions(model) -> list[str]:
    schema = model.model_json_schema()
    nodes = [(model.__name__, schema), *schema.get("$defs", {}).items()]
    missing = []
    for name, node in nodes:
        for prop, prop_schema in node.get("properties", {}).items():
            if prop == "schema_version":
                continue
            if "description" not in prop_schema:
                missing.append(f"{name}.{prop}")
    return missing


@pytest.mark.parametrize("model", AGENT_OUTPUTS, ids=lambda m: m.__name__)
def test_agent_outputs_describe_every_field(model):
    assert missing_descriptions(model) == []


def make_plan() -> Plan:
    task = Task(id="MAPS-001", description="d", acceptance_criteria=["c"])
    return Plan(keyword="MAPS", description="d", tasks=[task])


def make_report() -> TestReport:
    return TestReport(command="pytest", passed=True, failures=[], log_path="logs/x.log")


def test_input_contracts_construct():
    goal = Goal(objective="Add a map section")
    plan = make_plan()
    assert ArchitectInput(goal=goal).previous_plan is None
    assert CriticInput(goal=goal, plan=plan).plan == plan
    implement = ImplementInput(task=plan.tasks[0], phase="red", test_cmd="pytest")
    assert implement.last_report is None
    assert TesterInput(plan=plan, diff="", final_report=make_report(), test_cmd="pytest").diff == ""
    assert ReviewInput(plan=plan, diff="", final_report=make_report()).open_assumptions == []


def test_input_contracts_are_exported_for_schemas():
    for model in (ArchitectInput, CriticInput, ImplementInput, TesterInput, ReviewInput):
        assert model in ALL_CONTRACTS
