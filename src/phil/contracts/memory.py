from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract, Part


class MemoryAdd(Part):
    section: Literal["Commands", "Layout", "Conventions", "Gotchas"] = Field(
        description="The memory section the line belongs in."
    )
    text: str = Field(description="One line, at most 200 characters: a durable fact about this repository.")


class MemoryInput(Contract):
    memory: str = Field(description="Phil's current memory for this repository, each bullet tagged [phil] or [you].")
    agent_file: str = Field(description="The repository's AGENTS.md or CLAUDE.md text, empty when there is none.")
    test_cmd: str = Field(description="The test command the plan used, empty when there is none.")
    notes: list[str] = Field(default_factory=list, description="Notes agents recorded in `memory_notes` during the run.")
    evidence: list[str] = Field(
        default_factory=list, description="Commands agents ran during the run and what they showed."
    )
    review_issues: list[str] = Field(
        default_factory=list, description="Issues the newest review raised, one line each."
    )


class MemoryUpdate(Contract):
    add: list[MemoryAdd] = Field(
        default_factory=list,
        description=(
            "Durable facts about this repository, not about this one goal, that would save a later run a step. "
            "Nothing already in the memory or the agent file."
        ),
    )
    remove: list[str] = Field(
        default_factory=list,
        description="Exact text of [phil] bullets that proved wrong or stale. Never a [you] bullet.",
    )
    agents_md: list[str] = Field(
        default_factory=list,
        description="Lines worth suggesting for the repository's AGENTS.md. Nothing personal.",
    )
