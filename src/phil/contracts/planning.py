from typing import Literal

from pydantic import Field, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.common import Issue, SelfCheck

TASK_ID_PATTERN = r"^[A-Z]{3,6}-\d{3}$"
KEYWORD_PATTERN = r"^[A-Z]{3,6}$"


class Task(Part):
    id: str = Field(pattern=TASK_ID_PATTERN, description="KEYWORD-### id, e.g. MAPS-001.")
    description: str = Field(description="One atomic change a developer can test-drive in isolation.")
    acceptance_criteria: list[str] = Field(
        min_length=1, description="Observable behaviours a failing test can check. At least one."
    )
    files_hint: list[str] = Field(default=[], description="Repo-relative files this task most likely touches.")
    status: Literal["TODO", "DONE", "SKIPPED", "FAILED"] = Field(
        default="TODO", description="Always TODO in a new plan."
    )


class Plan(Contract):
    keyword: str = Field(pattern=KEYWORD_PATTERN, description="3-6 uppercase letters naming the objective.")
    description: str = Field(description="One or two sentences on the approach.")
    tasks: list[Task] = Field(min_length=1, description="Ordered atomic tasks; each leaves the app green.")
    test_cmd: str | None = Field(default=None, description="Command that runs the project's tests, e.g. 'uv run pytest'.")
    story_ref: str | None = Field(default=None, description="Roadmap story reference, if given in the goal.")
    critic_notes: list[str] = Field(default=[], description="Leave empty; filled from the plan critique.")

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
    verdict: Literal["ok", "revise"] = Field(description="revise only for problems worth another planning round.")
    issues: list[Issue] = Field(description="Concrete problems, each tied to a task_id where possible.")
    notes: list[str] = Field(description="Short notes for the user about the plan's risks.")
    self_check: SelfCheck = Field(description="Your self-check of this critique.")
