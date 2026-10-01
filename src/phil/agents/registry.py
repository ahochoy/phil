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
from phil.contracts.routing import Answer, AnswerInput, RouteInput, RouteJudgement

# The answerer's model-call budget: its last allowed call must answer (see phil.agents.factory).
ANSWER_MAX_MODEL_CALLS = 12
# The quick implementer's model-call budget, enforced the same way.
QUICK_IMPLEMENTER_MAX_MODEL_CALLS = 15

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
    "answer": AgentSpec(
        "answer", "answerer", AnswerInput, Answer, tools=("shell",), harness="light", shared_prompt=False,
        read_only_shell=True, max_model_calls=ANSWER_MAX_MODEL_CALLS,
    ),
    "quick_implementer": AgentSpec(
        "quick_implementer", "implementer", ImplementInput, TaskResult, tools=("shell",), writes_files=True,
        harness="light", max_model_calls=QUICK_IMPLEMENTER_MAX_MODEL_CALLS, prompt_name="implementer",
    ),
}


def get_spec(name: str) -> AgentSpec:
    return SPECS[name]
