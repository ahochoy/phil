import json
import re
from collections.abc import Set as AbstractSet
from functools import lru_cache
from typing import Any

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
# For agents without tools, which get the skills' text in their input instead of load_skill.
INLINED_TRUST_SENTENCE = (
    "These skills, installed by the user, are included below as guidance: "
    "your output contract and Phil's permissions take precedence."
)


QUERY_CAP = 4000  # characters of goal text a skill is matched against
# The input fields that carry the goal, the task or the user's message (spec §4.8). Strings under any
# other key are not matched; the keys below are not looked into at all (code, chat, or repo-wide text).
QUERY_KEYS = frozenset(
    {"objective", "goal", "title", "summary", "description", "message", "question", "request", "text",
     "acceptance", "acceptance_criteria"}
)
_SKIPPED_KEYS = frozenset(
    {"diff", "repo_overview", "files", "files_hint", "chat", "history", "conversation", "context", "worklog",
     "prior_attempt", "previous_plan", "last_report", "final_report", "recent_events", "feedback", "memory",
     "agent_file", "evidence", "code"}
)

Query = str | AbstractSet[str]


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - _STOP_WORDS


def query_words(query: Query) -> AbstractSet[str]:
    """The query's words, worked out once so scoring many skills doesn't redo it."""
    return _words(query) if isinstance(query, str) else query


@lru_cache(maxsize=4096)
def _skill_words(name: str, description: str) -> frozenset[str]:
    return frozenset(_words(re.sub(r"[-_]", " ", name) + " " + description))


def _collect(value: Any, parts: list[str], size: list[int]) -> None:
    if size[0] >= QUERY_CAP:
        return
    if isinstance(value, list):
        for item in value:
            _collect(item, parts, size)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key in _SKIPPED_KEYS:
            continue
        if key == "turns" and isinstance(item, list):  # a chat so far: what the user said, newest first
            texts = [turn.get("text") for turn in reversed(item) if isinstance(turn, dict) and turn.get("speaker") == "user"]
            _add([text for text in texts if isinstance(text, str)], parts, size)
        elif key in QUERY_KEYS and isinstance(item, str):
            _add([item], parts, size)
        elif key in QUERY_KEYS and isinstance(item, list) and all(isinstance(entry, str) for entry in item):
            _add(item, parts, size)
        else:
            _collect(item, parts, size)


def _add(texts: list[str], parts: list[str], size: list[int]) -> None:
    for text in texts:
        if text.strip() and size[0] < QUERY_CAP:
            parts.append(text.strip())
            size[0] += len(text) + 1


def skill_query(packet: Any) -> str:
    """The text a skill is matched against: the goal, task and user message fields of the packet's input,
    never its diff, repo overview, file lists or chat history. At most QUERY_CAP characters."""
    try:
        data = json.loads(packet.contract_json)
    except (ValueError, TypeError, AttributeError):
        return ""
    parts: list[str] = []
    _collect(data, parts, [0])
    return " ".join(parts)[:QUERY_CAP]


def score(query: Query, skill: Skill) -> float:
    """The share of the skill's words (name plus description) that appear in the query, which is text or
    the word set `query_words` made from it."""
    skill_words = _skill_words(skill.name, skill.description)
    if not skill_words:
        return 0.0
    return len(query_words(query) & skill_words) / len(skill_words)


def offered(catalog: Catalog, cache: LabelCache, config: PhilConfig, agent: str, query: Query) -> list[Skill]:
    """The skills this agent is told about: on, labelled for its stages, best match first, at most MAX_LISTED."""
    wanted = AGENT_STAGES.get(agent)
    if not wanted:
        return []
    words = query_words(query)
    matching = []
    for skill in catalog.skills:
        if skill.name in config.skills.off:
            continue
        stages, _ = stages_for(skill, cache, config)
        if stages and set(stages) & set(wanted):
            matching.append(skill)
    matching.sort(key=lambda skill: (-score(words, skill), skill.name))
    return matching[:MAX_LISTED]


def to_inline(offered: list[Skill], query: Query) -> list[Skill]:
    """The first MAX_INLINED offered skills that really match the query (for agents without tools)."""
    words = query_words(query)
    return [skill for skill in offered if score(words, skill) >= MIN_SCORE][:MAX_INLINED]


def skills_section(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = [f"- {skill.name}: {skill.description}" for skill in skills]
    return "\n".join(["## Skills", "", TRUST_SENTENCE, "", *lines])
