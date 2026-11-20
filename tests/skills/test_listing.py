from pathlib import Path

import pytest

from phil.config import PhilConfig, SkillsConfig
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LabelCache
from phil.skills.listing import (
    NOT_APPROVED,
    REPO_HINT,
    repo_has_skills,
    skill_folder_lines,
    skill_lines,
)
from phil.skills.runtime import SkillRuntime


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def skill(name: str, content: str | None = None) -> Skill:
    return Skill(name, f"{name} helps", Path("/skills") / name, "agents", content or f"hash-{name}")


def runtime(skills, hidden=(), unreadable=0, labels=None) -> SkillRuntime:
    cache = LabelCache()
    for content_hash, stages in (labels or {}).items():
        cache.put(content_hash, stages)
    return SkillRuntime(catalog=Catalog(skills=list(skills), hidden=list(hidden), unreadable=unreadable), cache=cache)


def config(**skills) -> PhilConfig:
    return PhilConfig(skills=SkillsConfig(**skills))


def test_nothing_approved_says_how_to_choose(home):
    assert skill_lines(None, config(), False, False) == [NOT_APPROVED]
    assert NOT_APPROVED == "No skill folders are approved. Run phil setup to choose them."


def test_nothing_approved_still_mentions_the_repo_skills(home):
    assert skill_lines(None, config(), True, False) == [NOT_APPROVED, REPO_HINT]
    assert REPO_HINT == "This repo has its own skills; turn them on with: phil skills repo on"


def test_lists_sources_then_each_offered_skill_with_where_its_stages_came_from(home):
    (home / ".agents" / "skills").mkdir(parents=True)
    layout, tests = skill("layout"), skill("tests")
    loaded = runtime([layout, tests], labels={layout.content_hash: ["design", "implementing"]})
    lines = skill_lines(loaded, config(folders=["agents"], stages={"tests": ["testing"]}), False, False)
    assert lines == [
        "Skill folders: ~/.agents/skills",
        "layout  design, implementing (label)",
        "tests  testing (override)",
    ]


def test_off_hidden_unlabelled_and_unreadable_are_counted_or_named(home):
    (home / ".agents" / "skills").mkdir(parents=True)
    layout, tests, plain, twin = skill("layout"), skill("tests"), skill("plain"), skill("layout", "other")
    loaded = runtime(
        [layout, tests, plain],
        hidden=[twin],
        unreadable=2,
        labels={layout.content_hash: ["design"], tests.content_hash: ["testing"]},
    )
    lines = skill_lines(loaded, config(folders=["agents"], off=["tests"]), False, False)
    assert lines == [
        "Skill folders: ~/.agents/skills",
        "layout  design (label)",
        "Off: tests",
        "Hidden duplicates: layout",
        "Not labelled yet: 1",
        "Couldn't read: 2",
    ]


def test_a_skill_labelled_for_no_stage_is_not_listed(home):
    (home / ".agents" / "skills").mkdir(parents=True)
    odd = skill("odd")
    lines = skill_lines(runtime([odd], labels={odd.content_hash: []}), config(folders=["agents"]), False, False)
    assert lines == ["Skill folders: ~/.agents/skills"]


def test_approved_repo_skills_are_a_source(home):
    (home / ".agents" / "skills").mkdir(parents=True)
    lines = skill_lines(None, config(folders=["agents"]), True, True)
    assert lines == ["Skill folders: ~/.agents/skills, this repo's skills"]


def test_names_are_cleaned_for_display(home):
    (home / ".agents" / "skills").mkdir(parents=True)
    nasty = skill("bad\x1b[31mname")
    lines = skill_lines(runtime([nasty], labels={nasty.content_hash: ["design"]}), config(folders=["agents"]), False, False)
    assert "\x1b" not in "".join(lines)


def test_repo_has_skills(tmp_path):
    assert repo_has_skills(tmp_path) is False
    (tmp_path / ".agents" / "skills").mkdir(parents=True)
    assert repo_has_skills(tmp_path) is False
    folder = tmp_path / ".agents" / "skills" / "layout"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: layout\ndescription: d\n---\n", encoding="utf-8")
    assert repo_has_skills(tmp_path) is True


def test_skill_folder_lines_are_one_per_approved_path(home, tmp_path):
    (home / ".agents" / "skills").mkdir(parents=True)
    (home / ".claude" / "skills").mkdir(parents=True)
    assert skill_folder_lines(config(folders=["agents", "claude"]), None, None) == [
        "Skill folders Phil's agents can read:",
        "  ~/.agents/skills",
        "  ~/.claude/skills",
    ]


def test_skill_folder_lines_are_empty_with_nothing_approved(home):
    assert skill_folder_lines(config(), None, None) == []
