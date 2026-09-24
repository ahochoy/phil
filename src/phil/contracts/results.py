from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract
from phil.contracts.common import Issue, SelfCheck


class TaskResult(Contract):
    phase: Literal["red", "green"]
    summary: str = Field(max_length=600)
    files_changed: list[str]
    tests_added: list[str]
    self_check: SelfCheck


class TestReport(Contract):
    __test__ = False  # not a pytest test class

    command: str
    passed: bool
    failures: list[str]
    log_path: str
    new_failures_vs_baseline: list[str] = []


class TesterReport(Contract):
    tests_added: list[str]
    issues: list[Issue]
    self_check: SelfCheck


class Review(Contract):
    verdict: Literal["approve", "changes"]
    issues: list[Issue]
    assumption_resolutions: list[str]
    self_check: SelfCheck
