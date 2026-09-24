from phil.agents.spec import AgentSpec
from phil.contracts import (
    ArchitectInput,
    CriticInput,
    ImplementInput,
    Plan,
    PlanCritique,
    Review,
    ReviewInput,
    TaskResult,
    TesterInput,
    TesterReport,
)

SPECS: dict[str, AgentSpec] = {
    "architect": AgentSpec("architect", "architect", ArchitectInput, Plan),
    "critic": AgentSpec("critic", "critic", CriticInput, PlanCritique),
    "implementer": AgentSpec(
        "implementer", "implementer", ImplementInput, TaskResult, tools=("shell",), writes_files=True
    ),
    "tester": AgentSpec("tester", "tester", TesterInput, TesterReport, tools=("shell",), writes_files=True),
    "reviewer": AgentSpec("reviewer", "reviewer", ReviewInput, Review),
}


def get_spec(name: str) -> AgentSpec:
    return SPECS[name]
