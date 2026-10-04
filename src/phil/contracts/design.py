from pydantic import Field, field_validator, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.interface import Goal

MAX_APPROACHES = 3


class Approach(Part):
    name: str = Field(description="A short name, 2 to 5 words.")
    summary: str = Field(description="One or two sentences: what gets built and where, in the repo's terms.")
    tradeoffs: list[str] = Field(default=[], description="1 to 3 short trade-offs: costs, risks, what it rules out.")


class Approaches(Contract):
    options: list[Approach] = Field(min_length=2, description="2 or 3 genuinely different ways to build the goal.")
    recommended: int = Field(default=0, description="The index in `options` (from 0) of the approach you recommend.")
    reason: str = Field(default="", description="One sentence: why you recommend it.")

    @field_validator("options")
    @classmethod
    def _at_most_three(cls, value: list[Approach]) -> list[Approach]:
        return value[:MAX_APPROACHES]

    @model_validator(mode="after")
    def _recommended_in_range(self) -> "Approaches":
        if not 0 <= self.recommended < len(self.options):
            self.recommended = 0
        return self


class DesignInput(Contract):
    goal: Goal
    repo_overview: str = ""
