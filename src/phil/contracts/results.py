from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.common import Issue, SelfCheck


MAX_WORKLOG_PATHS = 50
MAX_WORKLOG_NOTES = 5
MAX_WORKLOG_NOTE_CHARS = 200


def _clip_paths(value: object) -> object:
    return value[:MAX_WORKLOG_PATHS] if isinstance(value, list) else value


class Worklog(Part):
    """A hand-off note between attempts, as the implementer writes it. Its limits clip rather than
    reject: an over-long worklog must never fail a task's output and force a whole agent re-run.
    The limits stay in the field constraints so the schema still shows them as guidance.

    What the attempt read is not the model's to write: the engine records it from the file tools
    (`AttemptWorklog.files_read`). A stray `files_read` from the model is dropped, not rejected."""

    files_changed: list[str] = Field(
        default_factory=list, max_length=50, description="Repo-relative paths you created, edited, or deleted."
    )
    notes: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 short notes for your next attempt: what you tried, what failed, what's next.",
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_model_files_read(cls, value: object) -> object:
        if cls is Worklog and isinstance(value, dict) and "files_read" in value:
            return {key: item for key, item in value.items() if key != "files_read"}
        return value

    @field_validator("files_changed", mode="before")
    @classmethod
    def _clip_changed(cls, value: object) -> object:
        return _clip_paths(value)

    @field_validator("notes", mode="before")
    @classmethod
    def _clip_notes(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [
            note[:MAX_WORKLOG_NOTE_CHARS] if isinstance(note, str) else note for note in value[:MAX_WORKLOG_NOTES]
        ]


class AttemptWorklog(Worklog):
    """The worklog the engine stores per task and hands to the next attempt: the implementer's
    note plus `files_read`, which the engine fills from the file tools' paths."""

    files_read: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Repo-relative paths your previous attempt read or listed with the file tools.",
    )

    @field_validator("files_read", mode="before")
    @classmethod
    def _clip_read(cls, value: object) -> object:
        return _clip_paths(value)


class TaskResult(Contract):
    phase: Literal["red", "green"] = Field(description="The phase you were asked to do.")
    summary: str = Field(max_length=600, description="What you changed and why, in a few terse lines.")
    files_changed: list[str] = Field(description="Repo-relative paths you created, edited, or deleted.")
    tests_added: list[str] = Field(description="Repo-relative test files you created or extended.")
    self_check: SelfCheck = Field(description="Your self-check of this work.")
    worklog: Worklog = Field(
        default_factory=Worklog,
        description="A short record of this attempt, handed to your next attempt at the same task.",
    )


class TestReport(Contract):
    __test__ = False  # not a pytest test class

    command: str
    passed: bool
    failures: list[str]
    log_path: str
    new_failures_vs_baseline: list[str] = []
    passed_count: int | None = None
    skipped_count: int | None = None
    exit_code: int | None = None


class TesterReport(Contract):
    __test__ = False  # not a pytest test class

    tests_added: list[str] = Field(description="Repo-relative test files you added (integration, E2E, edge cases).")
    issues: list[Issue] = Field(description="Defects found, including weak or misleading unit tests.")
    self_check: SelfCheck = Field(description="Your self-check of this testing pass.")


class Review(Contract):
    verdict: Literal["approve", "changes"] = Field(description="approve only if no blocker or major issue remains.")
    issues: list[Issue] = Field(description="Problems in the diff, most severe first.")
    assumption_resolutions: list[str] = Field(
        description="For each open assumption: 'confirmed: ...' or 'issue raised: ...'."
    )
    self_check: SelfCheck = Field(description="Your self-check of this review.")
