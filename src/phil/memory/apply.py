"""Validation of the memory agent's update (spec 2026-10-15 §4.3): code decides what is written, not the model."""

import logging
import re
import unicodedata
from dataclasses import dataclass

from phil.contracts.memory import MemoryUpdate
from phil.memory.doc import MAX_BULLET_CHARS, Bullet, MemoryDoc
from phil.workspace.shell import is_secret_name

logger = logging.getLogger(__name__)

MAX_ADDS = 8
MAX_SUGGESTIONS = 5
_SHORT_CHARS = 40

_KEY_PREFIX = re.compile(r"(?<![A-Za-z0-9])(sk-|ghp_|gho_|github_pat_|xox[abp]-|AKIA|AIza)[A-Za-z0-9_\-]{8,}")
_NAME_VALUE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(\S+)")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")


def looks_secret(text: str) -> bool:
    if _KEY_PREFIX.search(text) or "-----BEGIN" in text:
        return True
    if any(is_secret_name(m.group(1)) for m in _NAME_VALUE.finditer(text)):
        return True
    return any(
        any(c.isalpha() for c in m.group()) and any(c.isdigit() for c in m.group())
        for m in _LONG_TOKEN.finditer(text)
    )


def check_line(text: str) -> str | None:
    """Why `text` can't be a memory line or suggestion, or None when it can."""
    stripped = text.strip()
    if not stripped:
        return "empty"
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in stripped):
        return "more than one line"
    if "<!--" in stripped or "-->" in stripped:
        return "contains an HTML comment marker"
    if len(stripped) > MAX_BULLET_CHARS:
        return "longer than 200 characters"
    if looks_secret(stripped):
        return "looks like it contains a secret"
    return None


def normalise(text: str) -> str:
    return " ".join(text.casefold().split())


@dataclass
class Applied:
    added: list[Bullet]
    removed: list[str]
    dropped: list[Bullet]
    suggestions: list[str]


def _agent_file_lines(agent_file: str) -> set[str]:
    lines = set()
    for raw in agent_file.splitlines():
        line = raw.strip()
        if line.startswith(("- ", "* ")):
            line = line[2:]
        if line.strip():
            lines.add(normalise(line))
    return lines


def apply_update(doc: MemoryDoc, update: MemoryUpdate, *, agent_file: str, pending: list[str]) -> Applied:
    removed = [text for text in update.remove if doc.remove(text, phil_only=True)]
    for text in update.remove:
        if text not in removed:
            logger.debug("memory: ignored removal of %r (no such Phil bullet)", text)

    known = _agent_file_lines(agent_file) | {normalise(b.text) for b in doc.bullets()}
    added: list[Bullet] = []
    for item in update.add:
        if len(added) >= MAX_ADDS:
            logger.debug("memory: ignored addition %r (limit of %d)", item.text, MAX_ADDS)
            continue
        if (problem := check_line(item.text)) is not None:
            logger.debug("memory: rejected addition %r (%s)", item.text, problem)
            continue
        key = normalise(item.text)
        if key in known:
            logger.debug("memory: ignored addition %r (already known)", item.text)
            continue
        bullet = Bullet(item.section, item.text.strip(), True)
        doc.add(bullet)
        added.append(bullet)
        known.add(key)

    seen = _agent_file_lines(agent_file) | {normalise(p) for p in pending}
    suggestions: list[str] = []
    for text in update.agents_md:
        if len(suggestions) >= MAX_SUGGESTIONS:
            logger.debug("memory: ignored suggestion %r (limit of %d)", text, MAX_SUGGESTIONS)
            continue
        if (problem := check_line(text)) is not None:
            logger.debug("memory: rejected suggestion %r (%s)", text, problem)
            continue
        key = normalise(text)
        if key in seen:
            logger.debug("memory: ignored suggestion %r (already known)", text)
            continue
        suggestions.append(text.strip())
        seen.add(key)

    return Applied(added=added, removed=removed, dropped=doc.fit(), suggestions=suggestions)


def memory_line(applied: Applied) -> str | None:
    parts: list[str] = []
    if applied.added:
        items = "; ".join(
            f"{b.section}: {b.text[:_SHORT_CHARS] + '…' if len(b.text) > _SHORT_CHARS else b.text}"
            for b in applied.added
        )
        parts.append(f"+{len(applied.added)} ({items})")
    gone = len(applied.removed) + len(applied.dropped)
    if gone:
        parts.append(f"\N{MINUS SIGN}{gone}")
    if applied.suggestions:
        n = len(applied.suggestions)
        parts.append("1 suggestion for AGENTS.md" if n == 1 else f"{n} suggestions for AGENTS.md")
    if not parts:
        return None
    return "Memory: " + " · ".join([*parts, "/memory to review"])
