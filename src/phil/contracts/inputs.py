from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract
from phil.contracts.interface import Goal, RunStatus
from phil.contracts.planning import Plan, PlanCritique, Task
from phil.contracts.results import TestReport, Worklog


class ArchitectInput(Contract):
    goal: Goal
    repo_overview: str = ""
    previous_plan: Plan | None = None
    critique: PlanCritique | None = None


class CriticInput(Contract):
    goal: Goal
    plan: Plan


class IntakeInput(Contract):
    message: str
    previous_goal: Goal | None = None
    answers: list[str] = []
    repo_overview: str = ""


class ImplementInput(Contract):
    task: Task
    phase: Literal["red", "green"]
    test_cmd: str
    last_report: TestReport | None = None
    feedback: list[str] = Field(default_factory=list)
    worklog: Worklog | None = None
    diff: str = ""


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
