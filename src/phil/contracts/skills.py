from typing import Literal

from pydantic import Field

from phil.config import STAGES
from phil.contracts.base import Contract

Stage = Literal["planning", "design", "implementing", "testing", "reviewing"]

assert set(Stage.__args__) == set(STAGES)  # type: ignore[attr-defined]  # one list of stages, two spellings


class SkillToLabel(Contract):
    name: str
    description: str


class SkillLabelInput(Contract):
    skills: list[SkillToLabel]
    stages: list[str] = Field(description="The stages of work a skill can help with.")


class SkillLabel(Contract):
    name: str = Field(description="The skill's name, exactly as given.")
    stages: list[Stage] = Field(
        description="The stages this skill helps with; empty when it has nothing to do with software work."
    )


class SkillLabels(Contract):
    labels: list[SkillLabel] = Field(description="One label for every skill given.")
