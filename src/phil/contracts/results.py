from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract
from phil.contracts.common import Issue, SelfCheck


class TaskResult(Contract):
    phase: Literal["red", "green"] = Field(description="The phase you were asked to do.")
    summary: str = Field(max_length=600, description="What you changed and why, in a few terse lines.")
    files_changed: list[str] = Field(description="Repo-relative paths you created, edited, or deleted.")
    tests_added: list[str] = Field(description="Repo-relative test files you created or extended.")
    self_check: SelfCheck = Field(description="Your self-check of this work.")


class TestReport(Contract):
    __test__ = False  # not a pytest test class

    command: str
    passed: bool
    failures: list[str]
    log_path: str
    new_failures_vs_baseline: list[str] = []
    passed_count: int | None = None
    skipped_count: int | None = None


class TesterReport(Contract):
    __test__ = False  # not a pytest test class

    tests_added: list[str] = Field(description="Repo-relative test files you added (integration, E2E, edge cases).")
    issues: list[Issue] = Field(description="Defects found, including weak or misleading unit tests.")
    self_check: SelfCheck = Field(description="Your self-check of this testing pass.")


class Review(Contract):
    verdict: Literal["approve", "changes"] = Field(description="approve only if no blocker or major issue remains.")
    issues: list[Issue] = Field(description="Problems in the diff, most severe first.")
    assumption_resolutions: list[str] = Field(
        description="For each open assumption: 'confirmed: ...' or 'issue raised: ...'."
    )
    self_check: SelfCheck = Field(description="Your self-check of this review.")
