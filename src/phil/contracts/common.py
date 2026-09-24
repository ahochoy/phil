from typing import Literal

from phil.contracts.base import Part


class Claim(Part):
    statement: str
    command: str | None = None
    observed_output: str | None = None


class SelfCheck(Part):
    assumptions: list[str]
    evidence: list[Claim]
    risks: list[str]
    unverified: list[str]
    out_of_scope: list[str]


class Issue(Part):
    task_id: str | None = None
    file: str | None = None
    line: int | None = None
    severity: Literal["blocker", "major", "minor"]
    note: str
