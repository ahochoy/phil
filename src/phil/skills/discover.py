import hashlib
import logging
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePath

import yaml

from phil.skills.sources import REPO_DIRS, REPO_KEY, Source, is_link, links_on_the_way

logger = logging.getLogger(__name__)

__all__ = ["Catalog", "Skill", "Source", "discover"]

MAX_SKILL_BYTES = 256 * 1024  # a bigger SKILL.md is not read at all
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path  # the resolved skill directory
    source: str  # the source key
    content_hash: str  # sha256 hex of the SKILL.md bytes


@dataclass
class Catalog:
    skills: list[Skill] = field(default_factory=list)
    hidden: list[Skill] = field(default_factory=list)  # same name as an earlier skill, different content
    unreadable: int = 0


def _parse(raw: bytes) -> tuple[str, str]:
    """The (name, description) of a SKILL.md, or ValueError when it has none."""
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("no frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("unterminated frontmatter")
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    name, description = data.get("name"), data.get("description")
    if not (isinstance(name, str) and name.strip() and isinstance(description, str) and description.strip()):
        raise ValueError("needs a name and a description")
    # Both go into agents' prompts, so they must be one plain line each (the Agent Skills limits).
    if any(unicodedata.category(char) == "Cc" for char in name) or "#" in name:
        raise ValueError("the name has a control character or #")
    name, description = " ".join(name.split()), " ".join(description.split())
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"the name is over {MAX_NAME_CHARS} characters")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"the description is over {MAX_DESCRIPTION_CHARS} characters")
    return name, description


def _load(directory: Path, source_key: str) -> Skill:
    resolved = directory.resolve(strict=True)
    skill_file = resolved / "SKILL.md"
    if not skill_file.is_file():
        raise OSError(f"{skill_file} is not a regular file")
    info = skill_file.stat()
    if info.st_size > MAX_SKILL_BYTES:
        raise OSError(f"{skill_file} is over {MAX_SKILL_BYTES} bytes")
    if info.st_nlink > 1:  # reads refuse a hard-linked file, so it can't be offered either
        raise OSError(f"{skill_file} is hard-linked")
    with skill_file.open("rb") as handle:
        raw = handle.read(MAX_SKILL_BYTES + 1)
    if len(raw) > MAX_SKILL_BYTES:
        raise OSError(f"{skill_file} is over {MAX_SKILL_BYTES} bytes")
    name, description = _parse(raw)
    return Skill(name, description, resolved, source_key, hashlib.sha256(raw).hexdigest())


def _repo_root(skills_dir: Path) -> Path:
    """The repo a repo skills folder (one of REPO_DIRS) sits in."""
    for folder in REPO_DIRS:
        parts = PurePath(folder).parts
        if skills_dir.parts[-len(parts):] == parts:
            return Path(*skills_dir.parts[: -len(parts)])
    return skills_dir


def discover(sources: list[Source]) -> Catalog:
    """Find the skills in `sources`, earlier sources first. A skill seen twice (a symlink, or identical
    content) counts once; a different skill with a taken name is `hidden`. Broken skills are counted."""
    catalog = Catalog()
    seen_paths: set[Path] = set()
    seen_hashes: set[str] = set()
    by_name: dict[str, Skill] = {}
    for source in sources:
        for root in source.dirs:
            if source.key == REPO_KEY and links_on_the_way(_repo_root(root), root):
                logger.info("skills: skipping %s (reached through a link)", root)
                continue
            try:
                children = sorted(root.iterdir())
            except OSError as error:
                logger.info("skills: cannot list %s (%s)", root, error)
                continue
            is_repo = source.key == REPO_KEY
            for child in children:
                try:
                    # A repo could point a link anywhere: its skills must be real folders and files.
                    if is_repo and (is_link(child) or is_link(child / "SKILL.md")):
                        logger.info("skills: skipping %s (a link in the repo's skills)", child)
                        continue
                    if child.is_symlink() and not child.exists():
                        raise OSError(f"{child} is a dangling link")
                    if not child.is_dir():
                        continue
                    if not (child / "SKILL.md").exists():
                        continue
                    skill = _load(child, source.key)
                except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as error:
                    logger.info("skills: skipping %s (%s)", child, error)
                    catalog.unreadable += 1
                    continue
                if skill.path in seen_paths or skill.content_hash in seen_hashes:
                    continue
                seen_paths.add(skill.path)
                seen_hashes.add(skill.content_hash)
                if skill.name in by_name:
                    catalog.hidden.append(skill)
                    continue
                by_name[skill.name] = skill
                catalog.skills.append(skill)
    return catalog
