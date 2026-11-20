from pathlib import Path

from phil.config import PhilConfig, SkillsConfig
from phil.setup import skill_labels
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LabelCache
from phil.skills.runtime import SkillRuntime


def skill(name: str) -> Skill:
    return Skill(name, f"{name} helps", Path("/skills") / name, "agents", f"hash-{name}")


def fake_world(monkeypatch, tmp_path, runtime, stages=None):
    config = PhilConfig(models={"low": "ollama:x", "high": "ollama:x"}, skills=SkillsConfig(stages=stages or {}))
    monkeypatch.setattr(skill_labels, "load_config", lambda root, overrides=(): config)
    monkeypatch.setattr(skill_labels, "load_runtime", lambda config, repo_root, paths: runtime)


def test_it_labels_only_the_approved_skills_without_a_label(monkeypatch, tmp_path):
    cache = LabelCache()
    cache.put("hash-b", ["design"])
    runtime = SkillRuntime(catalog=Catalog(skills=[skill("a"), skill("b"), skill("c")]), cache=cache)
    fake_world(monkeypatch, tmp_path, runtime, stages={"c": ["design"]})
    seen = []

    def labeller(ctx, skills, cache):
        seen.append([s.name for s in skills])
        assert ctx.skills is runtime
        return len(skills)

    assert skill_labels.label_after_setup(tmp_path, labeller=labeller) == (1, 1)
    assert seen == [["a"]]


def test_no_runtime_or_nothing_missing_is_zero(monkeypatch, tmp_path):
    fake_world(monkeypatch, tmp_path, None)
    assert skill_labels.label_after_setup(tmp_path, labeller=lambda *a: 1 / 0) == (0, 0)
    cache = LabelCache()
    cache.put("hash-a", [])
    fake_world(monkeypatch, tmp_path, SkillRuntime(catalog=Catalog(skills=[skill("a")]), cache=cache))
    assert skill_labels.label_after_setup(tmp_path, labeller=lambda *a: 1 / 0) == (0, 0)


def test_a_short_count_is_reported(monkeypatch, tmp_path):
    runtime = SkillRuntime(catalog=Catalog(skills=[skill("a"), skill("b")]), cache=LabelCache())
    fake_world(monkeypatch, tmp_path, runtime)
    assert skill_labels.label_after_setup(tmp_path, labeller=lambda ctx, skills, cache: 1) == (2, 1)
