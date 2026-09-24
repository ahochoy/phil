from typing import Literal

from pydantic import Field, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.common import Issue, SelfCheck

TASK_ID_PATTERN = r"^[A-Z]{3,6}-\d{3}$"
KEYWORD_PATTERN = r"^[A-Z]{3,6}$"


class Task(Part):
    id: str = Field(pattern=TASK_ID_PATTERN)
    description: str
    acceptance_criteria: list[str] = Field(min_length=1)
    files_hint: list[str] = []
    status: Literal["TODO", "DONE", "SKIPPED", "FAILED"] = "TODO"


class Plan(Contract):
    keyword: str = Field(pattern=KEYWORD_PATTERN)
    description: str
    tasks: list[Task] = Field(min_length=1)
    test_cmd: str | None = None
    story_ref: str | None = None
    critic_notes: list[str] = []

    @model_validator(mode="after")
    def _check_task_ids(self) -> "Plan":
        for task in self.tasks:
            if not task.id.startswith(f"{self.keyword}-"):
                raise ValueError(f"task {task.id} does not use keyword {self.keyword}")
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task ids")
        return self


class PlanCritique(Contract):
    verdict: Literal["ok", "revise"]
    issues: list[Issue]
    notes: list[str]
    self_check: SelfCheck
