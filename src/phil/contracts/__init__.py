from phil.contracts.base import Contract, Part
from phil.contracts.common import Claim, Issue, SelfCheck
from phil.contracts.inputs import ArchitectInput, CriticInput, ImplementInput, ReviewInput, TesterInput
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
    ArchitectInput,
    CriticInput,
    ImplementInput,
    TesterInput,
    ReviewInput,
]

__all__ = [
    "ALL_CONTRACTS",
    "ArchitectInput",
    "Brief",
    "Claim",
    "Contract",
    "CriticInput",
    "Decision",
    "Goal",
    "ImplementInput",
    "Issue",
    "ParkedItem",
    "Part",
    "Plan",
    "PlanCritique",
    "Ref",
    "Review",
    "ReviewInput",
    "RunStatus",
    "SelfCheck",
    "Task",
    "TaskResult",
    "TesterInput",
    "TesterReport",
    "TestReport",
]
