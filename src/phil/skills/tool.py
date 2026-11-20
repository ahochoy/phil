"""The `load_skill` tool: lets an agent read an approved skill's SKILL.md, or a text file inside that
skill's folder. It never reads outside the folder."""

import hashlib
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
_HARD_LINK = "it is a hard link"
SKILL_CHANGED = "it changed since Phil found it"


class SkillReadError(Exception):
    """A skill file was not read; the message is a fixed reason, safe to show the model."""


def _lexically_inside(file: str) -> bool:
    """True when `file` is a plain relative path with no `..`. Decided on the text alone, before the
    filesystem is touched (a UNC path would make a network connection)."""
    if not file or "\x00" in file:
        return False
    for kind in (PurePath, PureWindowsPath):
        path = kind(file)
        if path.is_absolute() or path.drive or path.root or path.anchor or ".." in path.parts:
            return False
    return not file.startswith(("\\", "/"))


def read_skill_file(skill: Skill, file: str | None = None) -> str:
    """The text of `file` (SKILL.md when None) inside the skill folder, at most MAX_FILE_BYTES.
    Raises SkillReadError with a fixed reason. Every read of skill text goes through here."""
    if file is not None and not _lexically_inside(file):
        raise SkillReadError(_OUTSIDE)
    root = skill.path.resolve()
    try:
        target = (root / (file if file is not None else "SKILL.md")).resolve(strict=True)  # follows symlinks
    except (OSError, ValueError, RuntimeError):
        raise SkillReadError(_UNREADABLE) from None
    if not target.is_relative_to(root):
        raise SkillReadError(_OUTSIDE)
    flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        fd = os.open(target, flags)
    except (OSError, ValueError):
        raise SkillReadError(_UNREADABLE) from None
    try:
        # Check the file we actually opened, not the path: it may have been swapped since the check above.
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SkillReadError(_NOT_A_FILE)
        if info.st_nlink != 1:  # a hard link can point outside the folder
            raise SkillReadError(_HARD_LINK)
        if not os.path.samestat(info, os.stat(target)):
            raise SkillReadError(_UNREADABLE)
        if not target.resolve(strict=True).is_relative_to(root):
            raise SkillReadError(_OUTSIDE)
        data = b""
        while len(data) < MAX_FILE_BYTES + 1:  # never more than MAX_FILE_BYTES + 1 bytes
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if file is None and not _matches(fd, data, skill.content_hash):
            raise SkillReadError(SKILL_CHANGED)
    except (OSError, ValueError, RuntimeError):
        raise SkillReadError(_UNREADABLE) from None
    finally:
        os.close(fd)
    if len(data) <= MAX_FILE_BYTES:
        try:
            return _newlines(data.decode("utf-8"))
        except UnicodeDecodeError:
            raise SkillReadError(_NOT_TEXT) from None
    remaining = info.st_size - MAX_FILE_BYTES
    data = data[:MAX_FILE_BYTES]
    if not _only_tail_is_cut(data):
        raise SkillReadError(_NOT_TEXT)
    return _newlines(data.decode("utf-8", errors="ignore")) + f"\n[truncated: {max(1, -(-remaining // 1024))} KB more]"


def _newlines(text: str) -> str:
    """`text` with Windows (`\\r\\n`) and old Mac (`\\r`) line endings as `\\n`, so a skill written on
    Windows reads the same in a prompt. The hash check runs on the bytes before this."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _matches(fd: int, head: bytes, content_hash: str) -> bool:
    """True when the whole open file (`head`, already read, then the rest of `fd`, streamed in
    bounded chunks) hashes to `content_hash`: the SKILL.md Phil found and the user approved."""
    digest = hashlib.sha256(head)
    while chunk := os.read(fd, MAX_FILE_BYTES):
        digest.update(chunk)
    return digest.hexdigest() == content_hash


def _only_tail_is_cut(data: bytes) -> bool:
    """True when `data` is valid UTF-8 apart from, at most, a character cut off at the end."""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError as error:
        return error.start >= len(data) - 3 and error.reason == "unexpected end of data"


def make_load_skill_tool(
    skills: list[Skill],
    *,
    role: str,
    notice: Callable[[str], None] | None,
    still_approved: Callable[[Skill], bool] | None = None,
) -> Callable[..., str]:
    """`still_approved` is asked before every read (SkillRuntime.still_approved: a repo's skills must
    still be the ones the user approved); a skill it refuses is not read."""
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
            if still_approved is not None and not still_approved(skill):
                raise SkillReadError(SKILL_CHANGED)
            text = read_skill_file(skill, file)
            if still_approved is not None and not still_approved(skill):  # changed while it was read
                raise SkillReadError(SKILL_CHANGED)
        except SkillReadError as refusal:
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
