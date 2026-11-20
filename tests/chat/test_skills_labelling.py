import json
import threading
from pathlib import Path

from phil.agents.invoke import AgentContext
from phil.config import PhilConfig, SkillsConfig
from phil.contracts.skills import SkillLabel, SkillLabels
from phil.skills.discover import Catalog, Skill
from phil.skills.labels import LABEL_PROMPT_VERSION, LabelCache, label_skills, labels_path
from phil.skills.runtime import SkillRuntime
from tests.chat.test_controller import run_chat


def skill(name: str) -> Skill:
    return Skill(name, f"{name} helps", Path("/skills") / name, "agents", f"hash-{name}")


def runtime_with(*skills: Skill, labelled: dict[str, list[str]] | None = None) -> SkillRuntime:
    cache = LabelCache()
    for name, stages in (labelled or {}).items():
        cache.put(f"hash-{name}", stages)
    return SkillRuntime(catalog=Catalog(skills=list(skills)), cache=cache)


def start_chat(calc_repo, monkeypatch, runtime, config=None, labeller=None):
    monkeypatch.setattr("phil.chat.controller.load_runtime", lambda config, root, paths: runtime)
    if labeller is not None:
        monkeypatch.setattr("phil.chat.controller.label_skills", labeller)
    box = {}

    def grab(controller):
        box["controller"] = controller
        thread = controller._label_thread
        if thread is not None:
            thread.join(timeout=10)
            assert not thread.is_alive()
        return None

    text, *_ = run_chat(calc_repo, [grab], {}, config=config)
    return box["controller"], text


def test_chat_start_labels_unlabelled_skills(calc_repo, monkeypatch, phil_home):
    runtime = runtime_with(skill("a"), skill("b"), labelled={"b": ["design"]})
    seen = []

    def fake_label(ctx, skills, cache, batch=25):
        seen.append(([s.name for s in skills], ctx.conn, threading.current_thread().name))
        for s in skills:
            cache.put(s.content_hash, ["testing"])
        cache.save()
        return len(skills)

    controller, _ = start_chat(calc_repo, monkeypatch, runtime, labeller=fake_label)

    names, conn, thread_name = seen[0]
    assert names == ["a"]  # b already has a label
    assert thread_name == "phil-skill-labels"
    assert conn is not controller.conn  # its own connection
    assert controller.ctx.skills.cache.get("hash-a") == ["testing"]  # the chat's own cache object
    saved = json.loads(labels_path().read_text())
    assert saved[f"{LABEL_PROMPT_VERSION}:hash-a"] == ["testing"]


def test_labelling_skips_overrides_and_starts_no_thread_when_nothing_is_missing(calc_repo, monkeypatch, phil_home):
    runtime = runtime_with(skill("a"), skill("b"), labelled={"b": ["design"]})
    calls = []
    config = PhilConfig(models={"low": "ollama:x", "high": "ollama:x"}, skills=SkillsConfig(stages={"a": ["design"]}))
    monkeypatch.setattr("phil.chat.controller.label_skills", lambda *a, **k: calls.append(a))
    monkeypatch.setattr("phil.chat.controller.load_runtime", lambda config, root, paths: runtime)
    box = {}

    def grab(controller):
        box["thread"] = controller._label_thread
        return None

    from tests.chat.test_controller import classic_config

    run_chat(calc_repo, [grab], {}, config=classic_config().model_copy(update={"skills": config.skills}))
    assert box["thread"] is None
    assert calls == []


def test_no_skills_means_no_thread(calc_repo, monkeypatch, phil_home):
    controller, _ = start_chat(calc_repo, monkeypatch, None)
    assert controller._label_thread is None


def test_only_the_approved_catalog_is_labelled(calc_repo, monkeypatch, phil_home):
    runtime = runtime_with(skill("a"))
    runtime.catalog.hidden.append(skill("hidden-one"))
    seen = []

    def fake_label(ctx, skills, cache, batch=25):
        seen.extend(s.name for s in skills)
        return 0

    start_chat(calc_repo, monkeypatch, runtime, labeller=fake_label)
    assert seen == ["a"]


def test_labelling_failure_is_silent(calc_repo, monkeypatch, phil_home):
    runtime = runtime_with(skill("a"))

    def boom(ctx, skills, cache, batch=25):
        raise RuntimeError("the factory raised")

    controller, text = start_chat(calc_repo, monkeypatch, runtime, labeller=boom)
    assert "factory raised" not in text and "RuntimeError" not in text
    assert controller._label_thread is not None and not controller._label_thread.is_alive()
    assert controller.ctx.skills.cache.get("hash-a") is None


def test_the_cache_is_safe_to_share_between_threads(phil_home):
    cache = LabelCache()
    errors = []

    def writer(n):
        try:
            for i in range(200):
                cache.put(f"h{n}-{i}", ["design"])
                cache.get(f"h{n}-{i}")
                if i % 50 == 0:
                    cache.save()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(json.loads(labels_path().read_text())) > 0
    assert hasattr(cache, "_lock")
