from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract
from phil.contracts.design import Approach
from phil.contracts.interface import Goal, RunStatus
from phil.contracts.planning import Plan, PlanCritique, Task
from phil.contracts.results import AttemptWorklog, TestReport


class ArchitectInput(Contract):
    goal: Goal
    repo_overview: str = ""
    previous_plan: Plan | None = None
    critique: PlanCritique | None = None
    detected_test_cmd: str | None = None
    prior_attempt: list[AttemptWorklog] = Field(
        default=[], description="When set, a quick attempt at this goal failed; its worklogs say what was tried and why it failed."
    )
    chosen_approach: Approach | None = Field(
        default=None, description="When set, the user chose this approach from the designer's proposals: plan it."
    )
    approach_note: str = Field(
        default="", description="When set, the user's own description of how to build it: plan that."
    )


class CriticInput(Contract):
    goal: Goal
    plan: Plan


class IntakeInput(Contract):
    message: str
    previous_goal: Goal | None = None
    answers: list[str] = []
    repo_overview: str = ""
    route_depth: Literal["answer", "quick", "full"] | None = None
    detected_test_cmd: str | None = None


class ImplementInput(Contract):
    task: Task
    phase: Literal["red", "green"]
    test_cmd: str
    last_report: TestReport | None = None
    feedback: list[str] = Field(default_factory=list)
    worklog: AttemptWorklog | None = None
    diff: str = ""
    continuing: bool = False  # True: the previous attempt's changes are still in the worktree


class TesterInput(Contract):
    __test__ = False  # not a pytest test class

    plan: Plan
    diff: str
    final_report: TestReport
    test_cmd: str


class ReviewInput(Contract):
    plan: Plan
    diff: str
    final_report: TestReport
    open_assumptions: list[str] = Field(default_factory=list)


class BtwInput(Contract):
    question: str
    goal: Goal | None = None
    plan: Plan | None = None
    run: RunStatus | None = None
    recent_events: list[str] = []
    pending_question: str | None = None
