import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from phil.skills.sources import Source

logger = logging.getLogger(__name__)

__all__ = ["Catalog", "Skill", "Source", "discover"]


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
    text = raw.decode("utf-8")
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
    return name.strip(), description.strip()


def _load(directory: Path, source_key: str) -> Skill:
    resolved = directory.resolve(strict=True)
    skill_file = resolved / "SKILL.md"
    if not skill_file.is_file():
        raise OSError(f"{skill_file} is not a regular file")
    raw = skill_file.read_bytes()
    name, description = _parse(raw)
    return Skill(name, description, resolved, source_key, hashlib.sha256(raw).hexdigest())


def discover(sources: list[Source]) -> Catalog:
    """Find the skills in `sources`, earlier sources first. A skill seen twice (a symlink, or identical
    content) counts once; a different skill with a taken name is `hidden`. Broken skills are counted."""
    catalog = Catalog()
    seen_paths: set[Path] = set()
    seen_hashes: set[str] = set()
    by_name: dict[str, Skill] = {}
    for source in sources:
        for root in source.dirs:
            try:
                children = sorted(root.iterdir())
            except OSError as error:
                logger.info("skills: cannot list %s (%s)", root, error)
                continue
            for child in children:
                try:
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
