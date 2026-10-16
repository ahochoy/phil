"""The memory listing `phil memory` and the chat both show (spec 2026-10-15 §2.4): plain strings, never
markup, so callers build `Text` from them."""

from phil.memory.agents_md import read_suggestions
from phil.memory.doc import SECTIONS
from phil.memory.store import read_memory
from phil.store.paths import ProjectPaths
from phil.text import printable

NO_MEMORY = "No memory yet for this repo."
NOT_RECOGNISED = "memory.md doesn't use Phil's sections; Phil reads it as written and won't add to it."


def memory_lines(paths: ProjectPaths) -> list[str]:
    """Bullets by section, numbered across the whole file in `bullets()` order, then the file's path and
    any pending AGENTS.md suggestions. Raises OSError when the memory file can't be read."""
    doc = read_memory(paths)
    bullets = doc.bullets()
    numbered = {id(bullet): n for n, bullet in enumerate(bullets, 1)}
    lines: list[str] = []
    for section in SECTIONS:
        in_section = [bullet for bullet in bullets if bullet.section == section]
        if not in_section:
            continue
        lines.append(section)
        for bullet in in_section:
            lines.append(f"  {numbered[id(bullet)]}. {printable(bullet.text)}  ({'phil' if bullet.by_phil else 'you'})")
    if not bullets:
        lines.append(NO_MEMORY)
    lines.append(f"Memory file: {paths.memory_path}")
    suggestions = read_suggestions(paths)
    if suggestions:
        lines.append(f"{len(suggestions)} suggestion(s) for AGENTS.md: phil memory suggestions")
    if not doc.recognised:
        lines.append(NOT_RECOGNISED)
    return lines
