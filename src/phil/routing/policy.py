"""The routing policy (spec §3.5): plain code over a model's judgement, so thresholds can change
without new inference."""

from dataclasses import dataclass

from phil.routing.classes import DEPTH
from phil.routing.types import Judgement

OVERRIDES = {"/ask": "answer", "/quick": "quick", "/full": "full"}


@dataclass(frozen=True)
class Route:
    depth: str | None  # None: intake decides
    source: str  # "forced", "jev", "llm" or "intake"
    reason: str  # "forced", "class", "needs_detail", "other", "low_confidence" or "unavailable"
    text: str  # the message, without an override prefix
    judgement: Judgement | None


def parse_override(text: str) -> tuple[str | None, str]:
    """(forced depth, the rest of the message) for `/ask`, `/quick` or `/full` as the first word
    (any case); (None, text) otherwise."""
    head, _, rest = text.strip().partition(" ")
    depth = OVERRIDES.get(head.lower())
    if depth is None:
        return None, text
    return depth, rest.strip()


def decide(
    judgement: Judgement | None, *, confidence_threshold: float, detail_threshold: float
) -> tuple[str | None, str]:
    """(depth or None for "intake decides", reason), checked in order."""
    if judgement is None:
        return None, "unavailable"
    if judgement.needs_detail >= detail_threshold:
        return None, "needs_detail"
    if judgement.task_class not in DEPTH:
        return None, "other"
    if judgement.confidence < confidence_threshold:
        return None, "low_confidence"
    return DEPTH[judgement.task_class], "class"
