"""The routing policy (spec §3.5): plain code over a model's judgement, so thresholds can change
without new inference."""

from dataclasses import dataclass

from phil.routing.classes import DEPTH
from phil.routing.types import Judgement

OVERRIDES = {"/ask": "answer", "/quick": "quick", "/full": "full"}


@dataclass(frozen=True)
class Route:
    depth: str | None  # None: intake decides
    source: str  # "forced" (a /ask, /quick or /full prefix), "fix_offer", "jev", "llm" or "intake"
    # "forced" (the depth wasn't classified), "class", "needs_detail", "other", "low_confidence" or "unavailable"
    reason: str
    text: str  # the message, without an override prefix
    judgement: Judgement | None
    fallback_reason: str | None = None  # why Jev failed, when it did


def parse_override(text: str) -> tuple[str | None, str]:
    """(forced depth, the rest of the message) for `/ask`, `/quick` or `/full` as the first word
    (any case, followed by any whitespace); (None, text) otherwise."""
    parts = text.strip().split(maxsplit=1)
    depth = OVERRIDES.get(parts[0].lower()) if parts else None
    if depth is None:
        return None, text
    return depth, parts[1].strip() if len(parts) > 1 else ""


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
