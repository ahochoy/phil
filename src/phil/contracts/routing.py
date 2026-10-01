from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract

TaskClass = Literal[
    "question", "diagnosis", "small_operation", "simple_change", "focused_fix",
    "feature", "refactor", "design", "broad_project", "other",
]


class RouteInput(Contract):
    request: str
    chat: list[str] = []
    repo: dict = {}
    classes: dict[str, str] = Field(description="Each class key with its description and examples.")


class RouteJudgement(Contract):
    task_class: TaskClass = Field(description="The one class that best fits the request.")
    confidence: float = Field(ge=0, le=1, description="How sure you are of task_class, 0 to 1.")
    needs_detail: float = Field(
        ge=0, le=1, description="Probability (0 to 1) the user must be asked something before work can start."
    )
