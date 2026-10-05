from typing import Annotated, Literal

from pydantic import Field, field_validator

from phil.contracts.base import Contract, Part
from phil.contracts.planning import QuickTask

# Objectives a flaky model call returns instead of a real goal (seen live from a stub output):
# rejected so intake never plans from one. Matched lower-cased and stripped.
_FILLER_OBJECTIVES = {"x", "placeholder", "todo", "tbd", "n/a", "none", "test", "goal", "objective"}


class Ref(Part):
    label: str
    path: str


class Decision(Part):
    question: str
    options: list[str]


MAX_OPTIONS = 4


class Question(Part):
    text: str = Field(description="The question, in one sentence.")
    options: list[str] = Field(
        default=[],
        description="2 to 4 short answers the user can pick from, most likely first; empty only when no sensible "
        "options exist. Don't add an \"other\" option: the chat adds one.",
    )
    why: str = Field(default="", description="Optional: one short line on what the answer changes.")

    @field_validator("options")
    @classmethod
    def _usable_options(cls, value: list[str]) -> list[str]:
        """0 or 2–4 options: blanks are dropped, extras cut, and a lone option makes it a free-text
        question (a slightly-off list shouldn't cost a retry)."""
        options = [option.strip() for option in value if option.strip()][:MAX_OPTIONS]
        return options if len(options) >= 2 else []


class Goal(Contract):
    objective: str
    constraints: list[str] = []
    non_goals: list[str] = []
    open_questions: list[Question] = Field(
        default=[],
        description="Questions whose answer would change the work, most important first; at most 3.",
    )
    approach_open: bool = Field(
        default=False,
        description="True when there are several reasonable ways to build this (layout, structure, library) and "
        "the user hasn't said which.",
    )
    story_ref: str | None = None
    depth: Literal["answer", "quick", "full"] | None = Field(default=None, description="how much process the work needs: `answer` (a question or a \"why is X broken\" diagnosis, no change), `quick` (one small, well-specified change), or `full` (anything needing design, several files, or a plan). Leave null while `open_questions` is non-empty.")
    task: QuickTask | None = Field(default=None, description="Only for depth quick: the one task that does the whole change.")

    @field_validator("objective")
    @classmethod
    def _check_objective_is_real(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped.split()) < 2 or stripped.lower() in _FILLER_OBJECTIVES:
            raise ValueError("objective must be a real sentence describing the user's goal, not a placeholder")
        return value

    @field_validator("open_questions", mode="before")
    @classmethod
    def _questions_from_text(cls, value: object) -> object:
        """A plain string is a free-text question, so saved chats and older outputs still load."""
        if isinstance(value, list):
            return [{"text": item} if isinstance(item, str) else item for item in value]
        return value


class Brief(Contract):
    headline: str = Field(max_length=120)
    status: str | None = None
    needs_you: list[Decision] = []
    points: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=5)
    details: list[Ref] = []
    parked: int = Field(default=0, ge=0)


class ParkedItem(Contract):
    id: str
    raised_by: str
    note: str
    why_not_now: str
    source: Ref
    status: Literal["open", "promoted", "dropped"] = "open"
    run_id: str | None = None


class RunStatus(Contract):
    run_id: str
    state: str
    tasks_done: int
    tasks_total: int
    current_node: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    needs_attention: str | None = None
