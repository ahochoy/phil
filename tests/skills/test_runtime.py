from pathlib import Path

import pytest

from phil.config import PhilConfig, SkillsConfig
from phil.skills import runtime as runtime_module
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LabelCache
from phil.skills.runtime import SkillRuntime, load_runtime
from phil.skills.sources import repo_digest, set_repo_approved
from phil.skills.tool import SKILL_CHANGED, make_load_skill_tool
from phil.store.paths import ProjectPaths


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def write_skill(folder: Path, name: str) -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} helps\n---\nBody\n", encoding="utf-8")


def test_no_approved_source_means_no_runtime(home):
    write_skill(home / ".agents" / "skills" / "layout", "layout")
    assert load_runtime(PhilConfig(), None, None) is None


def test_an_approved_source_without_skills_means_no_runtime(home):
    (home / ".agents" / "skills").mkdir(parents=True)
    assert load_runtime(PhilConfig(skills=SkillsConfig(folders=["agents"])), None, None) is None


def test_approved_skills_make_a_runtime(home):
    write_skill(home / ".agents" / "skills" / "layout", "layout")
    loaded = load_runtime(PhilConfig(skills=SkillsConfig(folders=["agents"])), None, None)
    assert isinstance(loaded, SkillRuntime)
    assert [skill.name for skill in loaded.catalog.skills] == ["layout"]
    assert isinstance(loaded.cache, LabelCache)


def approved_repo(tmp_path: Path) -> tuple[Path, ProjectPaths]:
    root = tmp_path / "repo"
    write_skill(root / ".agents" / "skills" / "layout", "layout")
    (root / ".agents" / "skills" / "layout" / "ref.md").write_text("reference", encoding="utf-8")
    paths = ProjectPaths("repo", home=tmp_path / "phil-home")
    set_repo_approved(paths, True, repo_digest(root))
    return root, paths


def test_a_repo_skill_edited_mid_run_is_refused_by_load_skill(home, tmp_path):
    root, paths = approved_repo(tmp_path)
    loaded = load_runtime(PhilConfig(), root, paths)
    assert loaded is not None and loaded.repo_root == root and loaded.paths == paths
    [skill] = loaded.catalog.skills
    assert skill.source == "repo" and loaded.still_approved(skill)
    tool = make_load_skill_tool(loaded.catalog.skills, role="coder", notice=None, still_approved=loaded.still_approved)
    assert tool("layout", "ref.md") == "reference"
    (skill.path / "ref.md").write_text("edited by an agent", encoding="utf-8")  # SKILL.md is unchanged
    assert not loaded.still_approved(skill)
    assert tool("layout") == f"Can't read SKILL.md from layout: {SKILL_CHANGED}."
    assert tool("layout", "ref.md") == f"Can't read ref.md from layout: {SKILL_CHANGED}."


def test_user_skills_skip_the_repo_check(home, monkeypatch):
    def unexpected(*_):
        raise AssertionError("a user's skill must not check the repo approval")

    monkeypatch.setattr(runtime_module, "repo_approved", unexpected)
    write_skill(home / ".agents" / "skills" / "layout", "layout")
    loaded = load_runtime(PhilConfig(skills=SkillsConfig(folders=["agents"])), None, None)
    assert loaded is not None
    [skill] = loaded.catalog.skills
    assert loaded.still_approved(skill)


def test_a_repo_skill_without_a_repo_is_not_approved(tmp_path):
    skill = Skill("layout", "d", tmp_path, "repo", "0" * 64)
    assert not SkillRuntime(Catalog(), LabelCache()).still_approved(skill)


def test_a_failure_is_logged_and_means_no_runtime(home, monkeypatch, caplog):
    def broken(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime_module, "approved_sources", broken)
    with caplog.at_level("INFO"):
        assert load_runtime(PhilConfig(skills=SkillsConfig(folders=["agents"])), None, None) is None
    assert "boom" in caplog.text
