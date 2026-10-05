from phil.agents.spec import ANSWER_NOW, FINISH_NOW, PLAN_NOW, PROPOSE_NOW, AgentSpec
from phil.contracts import (
    Approaches,
    ArchitectInput,
    Brief,
    BtwInput,
    CriticInput,
    DesignInput,
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
# The designer's model-call budget, enforced like the answerer's.
DESIGN_MAX_MODEL_CALLS = 8
# The architect's, likewise: every call resends the history so far, so an uncapped exploration
# grows its cost quadratically (one live plan took 46 calls and 1.56M input tokens).
ARCHITECT_MAX_MODEL_CALLS = 10

SPECS: dict[str, AgentSpec] = {
    "intake": AgentSpec("intake", "orchestrator", IntakeInput, Goal, harness="lean", end_on_text=True),
    "architect": AgentSpec(
        "architect", "architect", ArchitectInput, Plan, max_model_calls=ARCHITECT_MAX_MODEL_CALLS,
        cap_message=PLAN_NOW,
    ),
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
        read_only_shell=True, max_model_calls=ANSWER_MAX_MODEL_CALLS, cap_message=ANSWER_NOW,
    ),
    "quick_implementer": AgentSpec(
        "quick_implementer", "implementer", ImplementInput, TaskResult, tools=("shell",), writes_files=True,
        harness="light", max_model_calls=QUICK_IMPLEMENTER_MAX_MODEL_CALLS, prompt_name="implementer",
        cap_message=FINISH_NOW,
    ),
    "design": AgentSpec(
        "design", "designer", DesignInput, Approaches, tools=("shell",), harness="light", shared_prompt=False,
        read_only_shell=True, max_model_calls=DESIGN_MAX_MODEL_CALLS, cap_message=PROPOSE_NOW,
    ),
}


def get_spec(name: str) -> AgentSpec:
    return SPECS[name]
