"""Run names (spec 2026-10-16 §3.1): a short slug that tells runs apart on screen. Display only:
the run ID stays the run's identity, and naming never fails a goal or a run (a bad name is
replaced, never rejected)."""

import re

from phil.contracts import Goal

MAX_CHARS = 32
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){1,2}$")
STOP_WORDS = frozenset(
    "a an the to of for and or in on with add make create update fix change new some our my".split()
)


def normalise(name: str) -> str:
    """Intake's name trimmed and lower-cased, spaces and underscores as hyphens, repeats collapsed."""
    slug = re.sub(r"[\s_]+", "-", name.strip().lower())
    return re.sub(r"-{2,}", "-", slug)


def _joined(words: list[str]) -> str | None:
    """`words` joined by hyphens, dropping words from the end until it fits in MAX_CHARS; None once
    fewer than two are left (the pattern needs two)."""
    while len(words) >= 2:
        slug = "-".join(words)
        if len(slug) <= MAX_CHARS:
            return slug
        words = words[:-1]
    return None


def built_name(objective: str, run_id: str) -> str:
    """The name built from the objective: its first three words that aren't stop words; its own
    first words when fewer than two are left; `run-<id>` when even that gives fewer than two."""
    words = [word for word in (re.sub(r"[^a-z0-9]", "", raw) for raw in objective.lower().split()) if word]
    kept = [word for word in words if word not in STOP_WORDS]
    slug = _joined(kept[:3] if len(kept) >= 2 else words[:3])
    return slug or f"run-{run_id.removeprefix('r-')}"


def run_name(goal: Goal, run_id: str) -> str:
    """The name stored for a run started from `goal`: intake's name, normalised, when it is a
    slug of at most MAX_CHARS characters; otherwise the built name."""
    if goal.name:
        slug = normalise(goal.name)
        if len(slug) <= MAX_CHARS and SLUG.match(slug):
            return slug
    return built_name(goal.objective, run_id)
