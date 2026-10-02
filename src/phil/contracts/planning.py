from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.common import Issue, SelfCheck

TASK_ID_PATTERN = r"^[A-Z]{3,6}-\d{3}$"
KEYWORD_PATTERN = r"^[A-Z]{3,6}$"


class Task(Part):
    id: str = Field(pattern=TASK_ID_PATTERN, description="KEYWORD-### id, e.g. MAPS-001.")
    description: str = Field(description="One atomic change a developer can test-drive, or check, in isolation.")
    acceptance_criteria: list[str] = Field(
        min_length=1, description="Observable outcomes a failing test, or a check task's check_cmd, can confirm. At least one."
    )
    files_hint: list[str] = Field(default=[], description="Repo-relative files this task most likely touches.")
    status: Literal["TODO", "DONE", "SKIPPED", "FAILED"] = Field(
        default="TODO", description="Always TODO in a new plan."
    )
    verify: Literal["tdd", "check"] = Field(
        default="tdd",
        description=(
            "tdd: write a failing test, then make it pass. check: no testable behaviour (copy, markup, static "
            "assets, config, docs); make the change and pass check_cmd."
        ),
    )
    check_cmd: str | None = Field(
        default=None,
        description="check tasks only: one shell command, ideally one the repo already defines, that must exit 0.",
    )

    @model_validator(mode="after")
    def _check_verify_mode(self) -> "Task":
        if self.verify == "check" and not self.check_cmd:
            raise ValueError(f"task {self.id}: a check task needs a check_cmd")
        if self.verify == "tdd" and self.check_cmd is not None:
            raise ValueError(f"task {self.id}: check_cmd is only for check tasks")
        return self


_TASK_FIELDS = Task.model_fields


class QuickTask(Part):
    """Intake's one task for a quick goal. Lenient on purpose: a bad task must not fail the whole goal.
    `quick_plan` checks it as a `Task` and falls back to full planning when it isn't one (Ruling R9)."""

    # A `Task` instance validates here too (by its fields), so callers can pass either.
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    # The same fields and descriptions as Task, without its id pattern, criteria minimum or validators.
    id: str = Field(description=_TASK_FIELDS["id"].description)
    description: str = Field(description=_TASK_FIELDS["description"].description)
    acceptance_criteria: list[str] = Field(description=_TASK_FIELDS["acceptance_criteria"].description)
    files_hint: list[str] = Field(default=[], description=_TASK_FIELDS["files_hint"].description)
    verify: Literal["tdd", "check"] = Field(default="tdd", description=_TASK_FIELDS["verify"].description)
    check_cmd: str | None = Field(default=None, description=_TASK_FIELDS["check_cmd"].description)


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
