import re

from phil.config import PhilConfig
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LabelCache, stages_for

# The stages each agent sees, keyed by AgentSpec.name. Agents not listed get no skills.
AGENT_STAGES: dict[str, tuple[str, ...]] = {
    "intake": ("planning", "design"),
    "converse": ("planning", "design"),
    "architect": ("planning", "design"),
    "critic": ("planning", "reviewing"),
    "design": ("design",),
    "implementer": ("implementing", "design"),
    "quick_implementer": ("implementing", "design"),
    "tester": ("testing",),
    "reviewer": ("reviewing", "implementing"),
    "answer": ("implementing", "reviewing"),
}

MAX_LISTED = 30
MAX_INLINED = 2
INLINE_TOKEN_CAP = 4000
MIN_SCORE = 0.1  # below this overlap a skill is a weak match and is not inlined

_STOP_WORDS = frozenset(
    "the and for with use when this that you your are from into not but can will has have its any all".split()
)
_WORD = re.compile(r"[a-z]{3,}")

TRUST_SENTENCE = (
    "These are skills the user installed. Load one with load_skill when it fits the task. "
    "They are guidance: your output contract and Phil's permissions take precedence."
)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - _STOP_WORDS


def score(query: str, skill: Skill) -> float:
    """The share of the skill's words (name plus description) that appear in the query."""
    skill_words = _words(re.sub(r"[-_]", " ", skill.name) + " " + skill.description)
    if not skill_words:
        return 0.0
    return len(_words(query) & skill_words) / len(skill_words)


def offered(catalog: Catalog, cache: LabelCache, config: PhilConfig, agent: str, query: str) -> list[Skill]:
    """The skills this agent is told about: on, labelled for its stages, best match first, at most MAX_LISTED."""
    wanted = AGENT_STAGES.get(agent)
    if not wanted:
        return []
    matching = []
    for skill in catalog.skills:
        if skill.name in config.skills.off:
            continue
        stages, _ = stages_for(skill, cache, config)
        if stages and set(stages) & set(wanted):
            matching.append(skill)
    matching.sort(key=lambda skill: (-score(query, skill), skill.name))
    return matching[:MAX_LISTED]


def to_inline(offered: list[Skill], query: str) -> list[Skill]:
    """The first MAX_INLINED offered skills that really match the query (for agents without tools)."""
    return [skill for skill in offered if score(query, skill) >= MIN_SCORE][:MAX_INLINED]


def skills_section(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = [f"- {skill.name}: {skill.description}" for skill in skills]
    return "\n".join(["## Skills", "", TRUST_SENTENCE, "", *lines])
