"""The skills a chat or a run can use: found once, then carried on AgentContext."""

import logging
from dataclasses import dataclass
from pathlib import Path

from phil.config import PhilConfig
from phil.skills.discover import Catalog, Skill, discover
from phil.skills.labels import LabelCache
from phil.skills.sources import REPO_KEY, approved_sources, repo_approved
from phil.store.paths import ProjectPaths

logger = logging.getLogger(__name__)

# A tool agent capped below this many model calls gets no skills (the architect's revise cap of 4):
# a skill load would take too large a share of its calls.
MIN_CAP_FOR_SKILLS = 5


@dataclass
class SkillRuntime:
    catalog: Catalog
    cache: LabelCache
    # Where the repo's approval is checked again before each read of one of its skills.
    repo_root: Path | None = None
    paths: ProjectPaths | None = None

    def still_approved(self, skill: Skill) -> bool:
        """True for a user's skill; for a repo's skill, only while the repo's skills are still the
        ones the user approved (an agent that writes files could have edited them since)."""
        if skill.source != REPO_KEY:
            return True
        if self.repo_root is None or self.paths is None:
            return False
        try:
            return repo_approved(self.paths, self.repo_root)
        except Exception:
            logger.info("skills: repo approval not checked for %s", skill.name, exc_info=True)
            return False


def load_runtime(config: PhilConfig, repo_root: Path | None, paths: ProjectPaths | None) -> SkillRuntime | None:
    """The approved skills and their labels; None when no source is approved, none holds a skill, or
    anything fails (logged: skills are optional and never stop a chat or a run)."""
    try:
        sources = approved_sources(config, repo_root, paths)
        if not sources:
            return None
        catalog = discover(sources)
        if not catalog.skills:
            return None
        return SkillRuntime(catalog=catalog, cache=LabelCache.load(), repo_root=repo_root, paths=paths)
    except Exception as error:
        logger.warning("skills: loading failed, continuing without skills (%s)", error, exc_info=True)
        return None
