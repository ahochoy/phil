import httpx

from phil.agents.fake import FakeAgentFactory
from phil.agents.invoke import AgentContext
from phil.config import PhilConfig
from phil.contracts.routing import RouteJudgement, TaskClass
from phil.routing.classes import CLASSES
from phil.routing.classify import classify
from phil.store.db import connect
from tests.routing.test_jev import FIXTURE

STATE = {"request": "fix the typo", "chat": [], "repo": {"test_cmd": None, "files": [], "file_count": 0}}
LLM_SAYS = RouteJudgement(task_class="question", confidence=0.7, needs_detail=0.1)


def ctx(tmp_path, models, factory) -> AgentContext:
    return AgentContext(config=PhilConfig(models=models), conn=connect(tmp_path / "t.db"), layer="chat",
                        factory=factory)


def test_task_class_matches_classes():
    # Keep the contract's enum and the routing classes' own keys from drifting apart.
    import typing

    assert set(typing.get_args(TaskClass)) == set(CLASSES)


def test_llm_backend_when_no_typesafe_classifier(tmp_path):
    factory = FakeAgentFactory([LLM_SAYS])
    result = classify(ctx(tmp_path, {"low": "openrouter:l", "high": "openrouter:h"}, factory), STATE)
    j = result.judgement
    assert result.fallback_reason is None
    assert (j.source, j.task_class, j.confidence, j.probabilities) == ("llm", "question", 0.7, {"question": 1.0})


def test_jev_when_configured(tmp_path):
    models = {"low": "openrouter:l", "high": "openrouter:h", "classifier": "typesafe:jev-latest"}
    t = httpx.MockTransport(lambda r: httpx.Response(200, json=FIXTURE))
    j = classify(ctx(tmp_path, models, FakeAgentFactory([])), STATE, transport=t,
                 environ={"TYPESAFE_API_KEY": "k"}).judgement
    assert j.source == "jev" and j.task_class == "simple_change"


def test_jev_failure_falls_back_to_the_low_model(tmp_path):
    models = {"low": "openrouter:l", "high": "openrouter:h", "classifier": "typesafe:jev-latest"}
    t = httpx.MockTransport(lambda r: httpx.Response(429))
    factory = FakeAgentFactory([LLM_SAYS])
    result = classify(ctx(tmp_path, models, factory), STATE, transport=t, environ={"TYPESAFE_API_KEY": "k"})
    j = result.judgement
    assert result.fallback_reason == "http 429"
    assert j.source == "llm" and j.fallback_reason == "http 429"
    assert factory.built == [("route", "openrouter:l")]  # the fallback uses the low model, not typesafe


def test_llm_failure_has_no_judgement(tmp_path):
    factory = FakeAgentFactory([RuntimeError("boom"), RuntimeError("boom")])
    result = classify(ctx(tmp_path, {"low": "openrouter:l", "high": "openrouter:h"}, factory), STATE)
    assert result.judgement is None and result.fallback_reason is None


def test_jev_and_llm_failing_keeps_the_jev_reason(tmp_path):
    models = {"low": "openrouter:l", "high": "openrouter:h", "classifier": "typesafe:jev-latest"}
    t = httpx.MockTransport(lambda r: httpx.Response(429))
    factory = FakeAgentFactory([RuntimeError("boom"), RuntimeError("boom")])
    result = classify(ctx(tmp_path, models, factory), STATE, transport=t, environ={"TYPESAFE_API_KEY": "k"})
    assert result.judgement is None and result.fallback_reason == "http 429"


def test_jev_failing_with_no_low_model_keeps_the_jev_reason(tmp_path):
    models = {"high": "openrouter:h", "classifier": "typesafe:jev-latest"}
    t = httpx.MockTransport(lambda r: httpx.Response(429))
    factory = FakeAgentFactory([])
    result = classify(ctx(tmp_path, models, factory), STATE, transport=t, environ={"TYPESAFE_API_KEY": "k"})
    assert result.judgement is None and result.fallback_reason == "http 429"
    assert factory.built == []


def test_a_request_failure_falls_back_to_the_low_model(tmp_path):
    models = {"low": "openrouter:l", "high": "openrouter:h", "classifier": "typesafe:jev-latest"}
    t = httpx.MockTransport(lambda r: httpx.Response(200, json=FIXTURE))
    factory = FakeAgentFactory([LLM_SAYS])
    j = classify(ctx(tmp_path, models, factory), STATE, transport=t,
                 environ={"TYPESAFE_API_KEY": "ts-“smart”-quote"}).judgement
    assert j.source == "llm" and j.fallback_reason == "request failed"
    assert factory.built == [("route", "openrouter:l")]
