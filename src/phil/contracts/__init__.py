from phil.contracts.base import Contract, Part
from phil.contracts.common import Claim, Issue, SelfCheck
from phil.contracts.interface import Brief, Decision, Goal, ParkedItem, Ref, RunStatus
from phil.contracts.planning import Plan, PlanCritique, Task
from phil.contracts.results import Review, TaskResult, TesterReport, TestReport

ALL_CONTRACTS: list[type[Contract]] = [
    Plan,
    PlanCritique,
    TaskResult,
    TestReport,
    TesterReport,
    Review,
    Goal,
    Brief,
    ParkedItem,
    RunStatus,
]

__all__ = [
    "ALL_CONTRACTS",
    "Brief",
    "Claim",
    "Contract",
    "Decision",
    "Goal",
    "Issue",
    "ParkedItem",
    "Part",
    "Plan",
    "PlanCritique",
    "Ref",
    "Review",
    "RunStatus",
    "SelfCheck",
    "Task",
    "TaskResult",
    "TesterReport",
    "TestReport",
]
