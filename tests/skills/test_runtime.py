from pathlib import Path

import pytest

from phil.config import PhilConfig, SkillsConfig
from phil.skills import runtime as runtime_module
from phil.skills.labels import LabelCache
from phil.skills.runtime import SkillRuntime, load_runtime


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


def test_a_failure_is_logged_and_means_no_runtime(home, monkeypatch, caplog):
    def broken(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime_module, "approved_sources", broken)
    with caplog.at_level("INFO"):
        assert load_runtime(PhilConfig(skills=SkillsConfig(folders=["agents"])), None, None) is None
    assert "boom" in caplog.text
