from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract
from phil.contracts.interface import Goal
from phil.contracts.planning import Plan, PlanCritique, Task
from phil.contracts.results import TestReport


class ArchitectInput(Contract):
    goal: Goal
    repo_overview: str = ""
    previous_plan: Plan | None = None
    critique: PlanCritique | None = None


class CriticInput(Contract):
    goal: Goal
    plan: Plan


class ImplementInput(Contract):
    task: Task
    phase: Literal["red", "green"]
    test_cmd: str
    last_report: TestReport | None = None


class TesterInput(Contract):
    plan: Plan
    diff: str
    final_report: TestReport
    test_cmd: str


class ReviewInput(Contract):
    plan: Plan
    diff: str
    final_report: TestReport
    open_assumptions: list[str] = Field(default_factory=list)
