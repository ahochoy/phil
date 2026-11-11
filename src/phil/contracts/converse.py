"""The intake conversation (spec 2026-11-10 §4.2): its turns, the converse agent's input, and each
turn's output."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.interface import MAX_OPTIONS, Goal
from phil.contracts.routing import TaskClass

MIN_STEPS, MAX_STEPS = 2, 7


class ConverseTurn(Part):
    speaker: Literal["user", "phil"] = Field(description="Who said it.")
    text: str = Field(description="What they said.")
    choices: list[str] = Field(default=[], description="The choices Phil offered with this message, if any.")


class ConverseInput(Contract):
    turns: list[ConverseTurn]
    route_depth: Literal["answer", "quick", "full"] | None = None
    route_class: TaskClass | None = None
    repo_overview: str = ""
    detected_test_cmd: str | None = None
    must_decide: bool = False


class IntakeTurn(Contract):
    reply: str = Field(description="Your message to the user: a few sentences at most, plain prose, no filler.")
    choices: list[str] = Field(
        default=[],
        description="0, or 2 to 4 short answers the user can pick from when your question has a few clear "
        "alternatives, most likely first. Don't add an \"other\" option: the chat adds one.",
    )
    ready: bool = Field(description="True when you know enough to start: then `goal` and `steps` are required.")
    goal: Goal | None = Field(
        default=None,
        description="Only when ready: the goal, with depth `quick` (and its `task`) or `full`, and no open questions.",
    )
    steps: list[str] = Field(
        default=[],
        description="Only when ready: 2 to 7 one-line steps, in the user's terms, saying what Phil will do.",
    )

    @field_validator("reply")
    @classmethod
    def _says_something(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reply must say something")
        return value.strip()

    @field_validator("choices")
    @classmethod
    def _usable_choices(cls, value: list[str]) -> list[str]:
        choices = [choice.strip() for choice in value if choice.strip()]
        if len(choices) == 1 or len(choices) > MAX_OPTIONS:
            raise ValueError(f"choices must be empty or 2 to {MAX_OPTIONS} options")
        return choices

    @model_validator(mode="after")
    def _ready_means_a_goal(self) -> "IntakeTurn":
        if not self.ready:
            if self.goal is not None or self.steps:
                raise ValueError("a turn that isn't ready has no goal and no steps")
            return self
        if self.goal is None:
            raise ValueError("a ready turn needs its goal")
        if self.goal.depth not in ("quick", "full"):
            raise ValueError("a ready goal's depth is quick or full")
        if self.goal.open_questions:
            raise ValueError("a ready goal has no open questions: ask them in the reply instead")
        if not MIN_STEPS <= len(self.steps) <= MAX_STEPS:
            raise ValueError(f"a ready turn has {MIN_STEPS} to {MAX_STEPS} steps")
        return self
