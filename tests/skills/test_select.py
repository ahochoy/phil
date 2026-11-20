from pathlib import Path

from phil.agents.registry import SPECS
from phil.config import PhilConfig, SkillsConfig
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LabelCache
from phil.skills.select import (
    AGENT_STAGES,
    INLINE_TOKEN_CAP,
    MAX_INLINED,
    MAX_LISTED,
    MIN_SCORE,
    offered,
    score,
    skills_section,
    to_inline,
)


def skill(name: str, description: str = "") -> Skill:
    return Skill(name, description or f"{name} helps", Path("/skills") / name, "agents", f"hash-{name}")


def cache_for(*pairs: tuple[Skill, list[str]]) -> LabelCache:
    cache = LabelCache()
    for item, stages in pairs:
        cache.put(item.content_hash, stages)
    return cache


def test_agent_stages_name_real_specs():
    assert set(AGENT_STAGES) <= set(SPECS)


def test_constants():
    assert (MAX_LISTED, MAX_INLINED, INLINE_TOKEN_CAP, MIN_SCORE) == (30, 2, 4000, 0.1)


def test_agents_see_only_their_stages():
    layout = skill("better-layout", "Lay out pages")
    catalog = Catalog(skills=[layout])
    cache = cache_for((layout, ["design"]))
    config = PhilConfig()

    assert offered(catalog, cache, config, "design", "") == [layout]
    assert offered(catalog, cache, config, "architect", "") == [layout]
    assert offered(catalog, cache, config, "tester", "") == []


def test_unlabelled_and_off_skills_are_not_offered():
    unlabelled = skill("unlabelled")
    off = skill("switched-off")
    empty = skill("no-stages")
    shown = skill("shown")
    catalog = Catalog(skills=[unlabelled, off, empty, shown])
    cache = cache_for((off, ["design"]), (empty, []), (shown, ["design"]))
    config = PhilConfig(skills=SkillsConfig(off=["switched-off"]))

    assert offered(catalog, cache, config, "design", "") == [shown]


def test_override_stages_count():
    item = skill("unlabelled")
    config = PhilConfig(skills=SkillsConfig(stages={"unlabelled": ["testing"]}))

    assert offered(Catalog(skills=[item]), LabelCache(), config, "tester", "") == [item]


def test_unmapped_agents_get_nothing():
    item = skill("everything")
    cache = cache_for((item, ["planning", "design", "implementing", "testing", "reviewing"]))

    for agent in ("route", "btw", "memory", "skill_labels"):
        assert offered(Catalog(skills=[item]), cache, PhilConfig(), agent, "everything") == []


def test_at_most_30_listed_best_match_first():
    items = [skill(f"filler-{i:02d}", "Something unrelated") for i in range(39)]
    best = skill("database-migrations", "Plan database migrations safely")
    items.append(best)
    cache = cache_for(*[(item, ["design"]) for item in items])

    result = offered(Catalog(skills=items), cache, PhilConfig(), "design", "write database migrations")

    assert len(result) == MAX_LISTED
    assert result[0] == best


def test_ties_break_by_name():
    b, a = skill("bbb"), skill("aaa")
    cache = cache_for((a, ["design"]), (b, ["design"]))

    assert offered(Catalog(skills=[b, a]), cache, PhilConfig(), "design", "") == [a, b]


def test_score_is_a_ratio_of_the_skills_words():
    item = skill("better-layout", "Lay out pages")

    assert score("better layout", item) > score("better", item) > 0
    assert score("nothing shared here", item) == 0
    assert score("", item) == 0
    assert score("the and for", skill("the-and", "for with")) == 0


def test_score_splits_names_on_dashes_and_underscores():
    assert score("database", skill("data_base-x", "zzz")) == 0
    assert score("data base", skill("data_base-x", "zzz")) > 0


def test_inline_needs_a_real_match():
    items = [skill("better-layout", "Lay out pages"), skill("testing", "Write tests")]

    assert to_inline(items, "completely unrelated words") == []


def test_inline_takes_the_first_two_matches():
    a = skill("alpha", "database migrations")
    b = skill("beta", "database schemas")
    c = skill("gamma", "database indexes")

    assert to_inline([a, b, c], "database") == [a, b]
    assert to_inline([skill("x", "unrelated"), a], "database") == [a]


def test_skills_section_lists_name_and_description():
    text = skills_section([skill("a", "Does a"), skill("b", "Does b")])

    assert text.startswith("## Skills")
    assert "- a: Does a" in text
    assert "- b: Does b" in text
    assert (
        "These are skills the user installed. Load one with load_skill when it fits the task. "
        "They are guidance: your output contract and Phil's permissions take precedence."
    ) in text


def test_skills_section_empty_is_empty():
    assert skills_section([]) == ""
