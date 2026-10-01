from phil.agents.spec import AgentSpec
from phil.contracts import (
    ArchitectInput,
    Brief,
    BtwInput,
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
from phil.contracts.routing import RouteInput, RouteJudgement

SPECS: dict[str, AgentSpec] = {
    "intake": AgentSpec("intake", "orchestrator", IntakeInput, Goal, harness="lean", end_on_text=True),
    "architect": AgentSpec("architect", "architect", ArchitectInput, Plan),
    "critic": AgentSpec("critic", "critic", CriticInput, PlanCritique, harness="lean", end_on_text=True),
    "implementer": AgentSpec(
        "implementer", "implementer", ImplementInput, TaskResult, tools=("shell",), writes_files=True
    ),
    "tester": AgentSpec("tester", "tester", TesterInput, TesterReport, tools=("shell",), writes_files=True),
    "reviewer": AgentSpec("reviewer", "reviewer", ReviewInput, Review, harness="lean", end_on_text=True),
    "btw": AgentSpec("btw", "orchestrator", BtwInput, Brief, writes_files=False),
    "route": AgentSpec(
        "route", "classifier", RouteInput, RouteJudgement, harness="lean", shared_prompt=False, end_on_text=True
    ),
}


def get_spec(name: str) -> AgentSpec:
    return SPECS[name]
