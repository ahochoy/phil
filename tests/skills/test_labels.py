import json
from pathlib import Path

import pytest

from phil.agents.fake import FakeAgentFactory
from phil.agents.invoke import AgentContext
from phil.config import PhilConfig, RoleBudget, SkillsConfig
from phil.contracts.skills import SkillLabel, SkillLabels
from phil.skills.discover import Skill
from phil.skills.labels import LABEL_PROMPT_VERSION, LabelCache, label_skills, labels_path, stages_for
from phil.store.db import connect


def skill(name: str, content_hash: str | None = None) -> Skill:
    return Skill(name, f"{name} helps", Path("/skills") / name, "agents", content_hash or f"hash-{name}")


def says(*pairs: tuple[str, list[str]]) -> SkillLabels:
    return SkillLabels(labels=[SkillLabel(name=name, stages=stages) for name, stages in pairs])


def ctx(tmp_path, factory, models=None, skills=None) -> AgentContext:
    config = PhilConfig(models=models or {"low": "openrouter:l", "high": "openrouter:h"}, skills=skills or SkillsConfig())
    return AgentContext(config=config, conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)


def test_labels_path_is_in_phil_home(phil_home):
    assert labels_path() == phil_home / "skills.json"


def test_labels_are_cached_by_content_hash(tmp_path, phil_home):
    factory = FakeAgentFactory([says(("a", ["design"]))])
    cache = LabelCache.load()
    a = skill("a")

    assert label_skills(ctx(tmp_path, factory), [a], cache) == 1
    assert label_skills(ctx(tmp_path, factory), [a], cache) == 0

    assert len(factory.agent.calls) == 1
    assert cache.get(a.content_hash) == ["design"]
    assert json.loads(labels_path().read_text()) == {f"{LABEL_PROMPT_VERSION}:{a.content_hash}": ["design"]}
    assert LabelCache.load().get(a.content_hash) == ["design"]


def test_a_changed_skill_is_relabelled(tmp_path, phil_home):
    factory = FakeAgentFactory([says(("a", ["design"])), says(("a", ["testing"]))])
    cache = LabelCache.load()

    label_skills(ctx(tmp_path, factory), [skill("a", "h1")], cache)
    label_skills(ctx(tmp_path, factory), [skill("a", "h2")], cache)

    assert len(factory.agent.calls) == 2
    assert cache.get("h2") == ["testing"]


def test_override_beats_label(tmp_path, phil_home):
    cache = LabelCache.load()
    a = skill("a")
    cache.put(a.content_hash, ["design"])
    config = PhilConfig(models={"low": "x:l", "high": "x:h"}, skills=SkillsConfig(stages={"a": ["testing"]}))

    assert stages_for(a, cache, config) == (["testing"], "override")
    assert stages_for(a, cache, PhilConfig(models={"low": "x:l", "high": "x:h"})) == (["design"], "label")
    assert stages_for(skill("b"), cache, config) == (None, "unlabelled")


def test_a_skill_with_no_stages_is_labelled_not_unlabelled(tmp_path, phil_home):
    factory = FakeAgentFactory([says(("blog", []))])
    cache = LabelCache.load()
    blog = skill("blog")

    label_skills(ctx(tmp_path, factory), [blog], cache)

    assert stages_for(blog, cache, PhilConfig(models={"low": "x:l", "high": "x:h"})) == ([], "label")


def test_unknown_stages_are_dropped(tmp_path, phil_home):
    bad = {"labels": [{"name": "a", "stages": ["gardening"]}]}
    factory = FakeAgentFactory([bad, says(("a", ["reviewing"]))])
    cache = LabelCache.load()

    assert label_skills(ctx(tmp_path, factory), [skill("a")], cache) == 1

    assert cache.get("hash-a") == ["reviewing"]


def test_label_failure_leaves_skills_unlabelled(tmp_path, phil_home):
    factory = FakeAgentFactory([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    cache = LabelCache.load()
    a = skill("a")

    assert label_skills(ctx(tmp_path, factory), [a], cache) == 0

    assert stages_for(a, cache, PhilConfig(models={"low": "x:l", "high": "x:h"})) == (None, "unlabelled")


def test_systemone_classifier_labels_with_the_low_model(tmp_path, phil_home):
    models = {"classifier": "typesafe:judge", "low": "openai:x", "high": "openai:h"}
    factory = FakeAgentFactory([says(("a", ["design"]))])

    label_skills(ctx(tmp_path, factory, models=models), [skill("a")], LabelCache.load())

    assert factory.built == [("skill_labels", "openai:x")]


def test_a_plain_classifier_model_is_used(tmp_path, phil_home):
    models = {"classifier": "openai:cls", "low": "openai:x", "high": "openai:h"}
    factory = FakeAgentFactory([says(("a", ["design"]))])

    label_skills(ctx(tmp_path, factory, models=models), [skill("a")], LabelCache.load())

    assert factory.built == [("skill_labels", "openai:cls")]


def test_batches_are_saved_as_they_succeed(tmp_path, phil_home):
    factory = FakeAgentFactory([says(("a", ["design"]), ("b", ["testing"])), RuntimeError("x"), RuntimeError("x")])
    skills = [skill("a"), skill("b"), skill("c")]

    assert label_skills(ctx(tmp_path, factory), skills, LabelCache.load(), batch=2) == 2

    reloaded = LabelCache.load()
    assert reloaded.get("hash-a") == ["design"] and reloaded.get("hash-b") == ["testing"]
    assert reloaded.get("hash-c") is None


def test_a_missing_label_in_the_reply_leaves_that_skill_unlabelled(tmp_path, phil_home):
    factory = FakeAgentFactory([says(("a", ["design"]))])
    cache = LabelCache.load()

    assert label_skills(ctx(tmp_path, factory), [skill("a"), skill("b")], cache) == 1

    assert cache.get("hash-b") is None


def test_a_batch_too_large_for_the_budget_is_halved(tmp_path, phil_home):
    skills = [Skill(f"s{i}", "word " * 150, Path("/s") / f"s{i}", "agents", f"h{i}") for i in range(4)]
    factory = FakeAgentFactory(
        [says(("s0", ["design"]), ("s1", ["design"])), says(("s2", ["design"]), ("s3", ["design"]))]
    )
    context = ctx(tmp_path, factory)
    context.config.budget["classifier"] = RoleBudget(max_input_tokens=600)

    assert label_skills(context, skills, LabelCache.load(), batch=4) == 4
    assert len(factory.agent.calls) == 2


def test_a_single_skill_too_large_is_skipped(tmp_path, phil_home):
    big = Skill("big", "word " * 5000, Path("/s/big"), "agents", "hbig")
    factory = FakeAgentFactory([])
    context = ctx(tmp_path, factory)
    context.config.budget["classifier"] = RoleBudget(max_input_tokens=600)

    assert label_skills(context, [big], LabelCache.load()) == 0
    assert factory.agent.calls == []


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"1:h": "design"}'])
def test_corrupt_cache_file_is_treated_as_empty(phil_home, content):
    labels_path().parent.mkdir(parents=True, exist_ok=True)
    labels_path().write_text(content, encoding="utf-8")

    cache = LabelCache.load()

    assert cache.get("h") is None
