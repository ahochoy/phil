from typing import Annotated, Literal

from pydantic import Field, field_validator

from phil.contracts.base import Contract, Part
from phil.contracts.planning import QuickTask

# Objectives a flaky model call returns instead of a real goal (seen live from a stub output):
# rejected so intake never plans from one. Matched lower-cased and stripped.
_FILLER_OBJECTIVES = {"x", "placeholder", "todo", "tbd", "n/a", "none", "test", "goal", "objective"}


class Ref(Part):
    label: str
    path: str


class Decision(Part):
    question: str
    options: list[str]


class Goal(Contract):
    objective: str
    constraints: list[str] = []
    non_goals: list[str] = []
    open_questions: list[str] = []
    story_ref: str | None = None
    depth: Literal["answer", "quick", "full"] | None = Field(default=None, description="how much process the work needs: `answer` (a question or a \"why is X broken\" diagnosis, no change), `quick` (one small, well-specified change), or `full` (anything needing design, several files, or a plan). Leave null while `open_questions` is non-empty.")
    task: QuickTask | None = Field(default=None, description="Only for depth quick: the one task that does the whole change.")

    @field_validator("objective")
    @classmethod
    def _check_objective_is_real(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped.split()) < 2 or stripped.lower() in _FILLER_OBJECTIVES:
            raise ValueError("objective must be a real sentence describing the user's goal, not a placeholder")
        return value


class Brief(Contract):
    headline: str = Field(max_length=120)
    status: str | None = None
    needs_you: list[Decision] = []
    points: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=5)
    details: list[Ref] = []
    parked: int = Field(default=0, ge=0)


class ParkedItem(Contract):
    id: str
    raised_by: str
    note: str
    why_not_now: str
    source: Ref
    status: Literal["open", "promoted", "dropped"] = "open"
    run_id: str | None = None


class RunStatus(Contract):
    run_id: str
    state: str
    tasks_done: int
    tasks_total: int
    current_node: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    needs_attention: str | None = None
