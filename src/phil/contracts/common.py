from typing import Literal

from pydantic import Field

from phil.contracts.base import Part


class Claim(Part):
    statement: str = Field(description="What you claim is true.")
    command: str | None = Field(
        default=None, description="Exact command you ran to verify the claim. Never list a command you did not run."
    )
    observed_output: str | None = Field(default=None, description="Relevant output you observed, trimmed.")


class SelfCheck(Part):
    assumptions: list[str] = Field(description="Things you took as given without verifying.")
    evidence: list[Claim] = Field(description="Claims backed by a command you ran and what it showed.")
    risks: list[str] = Field(description="Ways this output could be wrong.")
    unverified: list[str] = Field(description="Things you could not check.")
    out_of_scope: list[str] = Field(
        description="Problems you noticed that are not your job now. Record them here instead of acting on them."
    )


class Issue(Part):
    task_id: str | None = Field(default=None, description="Task id the issue belongs to, if any.")
    file: str | None = Field(default=None, description="Repo-relative file path, if any.")
    line: int | None = Field(default=None, description="1-based line number, if any.")
    severity: Literal["blocker", "major", "minor"] = Field(
        description="blocker: must fix before merge; major: should fix; minor: optional."
    )
    note: str = Field(description="What is wrong and why it matters, in one or two sentences.")
