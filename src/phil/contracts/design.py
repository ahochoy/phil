from typing import Literal

from pydantic import Field, field_validator, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.converse import ConverseTurn
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
    visual: bool = Field(
        default=False,
        description="True only when the approaches differ in how something looks (layout, placement, styling, "
        "what is on screen). Touching UI code is not enough.",
    )

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
    conversation: list[ConverseTurn] | None = Field(
        default=None,
        description="When set, the user's conversation with Phil before planning. The goal summarises it; "
        "where they differ, the user's own words win.",
    )


SKETCH_MAX_LINES = 40
SKETCH_MAX_COLUMNS = 100
MOCKUP_MAX_CHARS = 10_000  # the picked mockup as sent on to the architect and implementers


class MockupDrawing(Part):
    name: str = Field(description="The approach's name, exactly as given.")
    html: str = Field(
        default="",
        description="For format `html`: one HTML fragment, not a whole page. Inline CSS in a <style> element or "
        "style attributes only; no <script>, no event-handler attributes, no external URLs or fonts. Draw images as "
        "inline SVG or `data:` URIs. Leave empty for format `sketch`.",
    )
    sketch: str = Field(
        default="",
        description="For format `sketch`: a plain-text drawing, at most 40 lines of 100 columns (longer is "
        "cut). Leave empty for format `html`.",
    )
    note: str = Field(default="", description="One short line on what to look at in this drawing, or what it leaves out.")

    @field_validator("sketch")
    @classmethod
    def _clip(cls, value: str) -> str:
        lines = value.split("\n")[:SKETCH_MAX_LINES]
        return "\n".join(line[:SKETCH_MAX_COLUMNS] for line in lines)


class MockupInput(Contract):
    goal: Goal
    approaches: list[Approach] = Field(description="The approaches to draw, one drawing each.")
    repo_overview: str = Field(default="", description="A short overview of the repository's tracked files.")
    note: str = Field(default="", description="What the user asked to change or emphasise; follow it when set.")
    previous: str = Field(
        default="",
        description="When set, the approach's previous drawing: change only what the note asks; keep the rest.",
    )
    format: Literal["html", "sketch"] = Field(
        description="`html` fills each drawing's `html`; `sketch` fills its `sketch`. Never both."
    )
    conversation: list[ConverseTurn] | None = Field(
        default=None,
        description="When set, the user's conversation with Phil before planning. The goal summarises it; "
        "where they differ, the user's own words win.",
    )


class Mockup(Contract):
    drawings: list[MockupDrawing] = Field(min_length=1, description="One drawing per approach, in the order given.")
