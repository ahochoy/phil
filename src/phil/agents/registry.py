from phil.agents.spec import AgentSpec
from phil.contracts import (
    ArchitectInput,
    CriticInput,
    Goal,
    ImplementInput,
    IntakeInput,
    Plan,
    PlanCritique,
    Review,
    ReviewInput,
    TaskResult,
    TesterInput,
    TesterReport,
)

SPECS: dict[str, AgentSpec] = {
    "intake": AgentSpec("intake", "orchestrator", IntakeInput, Goal, harness="lean"),
    "architect": AgentSpec("architect", "architect", ArchitectInput, Plan),
    "critic": AgentSpec("critic", "critic", CriticInput, PlanCritique, harness="lean"),
    "implementer": AgentSpec(
        "implementer", "implementer", ImplementInput, TaskResult, tools=("shell",), writes_files=True
    ),
    "tester": AgentSpec("tester", "tester", TesterInput, TesterReport, tools=("shell",), writes_files=True),
    "reviewer": AgentSpec("reviewer", "reviewer", ReviewInput, Review, harness="lean"),
}


def get_spec(name: str) -> AgentSpec:
    return SPECS[name]
