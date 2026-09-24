from typing import Annotated, Literal

from pydantic import Field

from phil.contracts.base import Contract, Part


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
