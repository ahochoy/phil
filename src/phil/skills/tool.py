"""The `load_skill` tool: lets an agent read an approved skill's SKILL.md, or a text file inside that
skill's folder. It never reads outside the folder."""

import logging
import os
import stat
from collections.abc import Callable
from pathlib import Path, PurePath, PureWindowsPath

from phil.skills.discover import Skill

logger = logging.getLogger(__name__)

MAX_LOADS = 3
MAX_FILE_BYTES = 64 * 1024

# Fixed reasons only: raw OS errors would let the model probe which outside paths exist.
_OUTSIDE = "it is outside the skill's folder"
_NOT_A_FILE = "it is not a file"
_NOT_TEXT = "it is not text"
_UNREADABLE = "it can't be read"


class _Refused(Exception):
    pass


def _lexically_inside(file: str) -> bool:
    """True when `file` is a plain relative path with no `..`. Decided on the text alone, before the
    filesystem is touched (a UNC path would make a network connection)."""
    if not file:
        return False
    for kind in (PurePath, PureWindowsPath):
        path = kind(file)
        if path.is_absolute() or path.drive or path.root or path.anchor or ".." in path.parts:
            return False
    return not file.startswith(("\\", "/"))


def _read_confined(skill: Skill, file: str | None) -> str:
    """The text of `file` (SKILL.md when None) inside the skill folder. Raises _Refused with a fixed reason."""
    if file is not None and not _lexically_inside(file):
        raise _Refused(_OUTSIDE)
    root = skill.path.resolve()
    try:
        target = (root / (file if file is not None else "SKILL.md")).resolve(strict=True)  # follows symlinks
    except (OSError, RuntimeError):
        raise _Refused(_UNREADABLE) from None
    if not target.is_relative_to(root):
        raise _Refused(_OUTSIDE)
    flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        fd = os.open(target, flags)
    except OSError:
        raise _Refused(_UNREADABLE) from None
    try:
        # Check the file we actually opened, not the path: it may have been swapped since the check above.
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:  # a hard link can point outside the folder
            raise _Refused(_NOT_A_FILE)
        if not os.path.samestat(info, os.stat(target)):
            raise _Refused(_UNREADABLE)
        if not target.resolve(strict=True).is_relative_to(root):
            raise _Refused(_OUTSIDE)
        data = b""
        while len(data) < MAX_FILE_BYTES + 1:  # never more than MAX_FILE_BYTES + 1 bytes
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
    except OSError:
        raise _Refused(_UNREADABLE) from None
    finally:
        os.close(fd)
    if len(data) <= MAX_FILE_BYTES:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused(_NOT_TEXT) from None
    remaining = info.st_size - MAX_FILE_BYTES
    data = data[:MAX_FILE_BYTES]
    if not _only_tail_is_cut(data):
        raise _Refused(_NOT_TEXT)
    return data.decode("utf-8", errors="ignore") + f"\n[truncated: {max(1, -(-remaining // 1024))} KB more]"


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
        except _Refused as refusal:
            logger.info("load_skill refused %s/%s: %s", name, file, refusal)
            return f"Can't read {file if file is not None else 'SKILL.md'} from {name}: {refusal}."
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
