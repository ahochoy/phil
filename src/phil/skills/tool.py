"""The `load_skill` tool: lets an agent read an approved skill's SKILL.md, or a text file inside that
skill's folder. It never reads outside the folder."""

import logging
from collections.abc import Callable
from pathlib import Path

from phil.skills.discover import Skill

logger = logging.getLogger(__name__)

MAX_LOADS = 3
MAX_FILE_BYTES = 64 * 1024


def _read_confined(skill: Skill, file: str | None) -> str:
    """The text of `file` (SKILL.md when None) inside the skill folder. Raises OSError or ValueError with
    a short reason when the file is outside the folder, not a regular file, or not UTF-8 text."""
    root = skill.path.resolve()
    target = (root / (file or "SKILL.md")).resolve(strict=True)  # an absolute `file` replaces root; checked below
    if not target.is_relative_to(root):
        raise ValueError("it is outside the skill's folder")
    if not target.is_file():
        raise ValueError("it is not a file")
    with target.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    extra = ""
    if len(data) > MAX_FILE_BYTES:
        remaining = target.stat().st_size - MAX_FILE_BYTES
        extra = f"\n[truncated: {max(1, -(-remaining // 1024))} KB more]"
        data = data[:MAX_FILE_BYTES]
        # cut on a character boundary: drop a partial trailing character, but nothing else
        text = data.decode("utf-8", errors="ignore") if _only_tail_is_cut(data) else None
        if text is None:
            raise ValueError("it is not text")
        return text + extra
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("it is not text") from None


def _only_tail_is_cut(data: bytes) -> bool:
    """True when `data` is valid UTF-8 apart from, at most, a character cut off at the end."""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError as error:
        return error.start >= len(data) - 3 and error.reason == "unexpected end of data"


def make_load_skill_tool(skills: list[Skill], *, role: str, notice: Callable[[str], None] | None) -> Callable[..., str]:
    by_name = {skill.name: skill for skill in skills}
    loads = 0
    announced: set[str] = set()

    def load_skill(name: str, file: str | None = None) -> str:
        """Read one of the listed skills' SKILL.md, or a text file inside that skill's folder."""
        nonlocal loads
        skill = by_name.get(name)
        if skill is None:
            return f"Unknown skill {name}. Available: {', '.join(by_name)}"
        if loads >= MAX_LOADS:
            return f"Skill limit reached ({MAX_LOADS} per task); continue without more skills."
        try:
            text = _read_confined(skill, file)
        except (OSError, ValueError) as error:
            reason = error.strerror if isinstance(error, OSError) and error.strerror else str(error)
            logger.info("load_skill refused %s/%s: %r", name, file, error)
            return f"Can't read {file or 'SKILL.md'} from {name}: {reason}."
        loads += 1
        if name not in announced:
            announced.add(name)
            if notice is not None:
                try:
                    notice(f"Using skill {name} ({role})")
                except Exception:
                    logger.debug("skill notice failed", exc_info=True)
        return text

    return load_skill
