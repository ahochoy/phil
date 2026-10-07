import json
from pathlib import Path

import httpx
import pytest

from phil.agents.providers import BUILTIN_PROVIDERS
from phil.routing import CLASSES
from phil.routing.jev import JevError, build_request, judge_jev, ping_jev

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jev_ok.json").read_text())
SPEC = BUILTIN_PROVIDERS["typesafe"]
SECRET = "ts-TESTSECRET0123456789abcdefABCDEF"
ENV = {"TYPESAFE_API_KEY": SECRET}
STATE = {"request": "fix the typo", "chat": [], "repo": {"test_cmd": None, "files": [], "file_count": 0}}


def transport(handler):
    return httpx.MockTransport(handler)


def test_build_request_asks_both_questions():
    body = build_request("jev-latest", STATE)
    assert body["model"] == "jev-latest" and body["state"] == STATE
    task_class = body["questions"]["task_class"]
    assert task_class["type"] == "choice" and set(task_class["criteria"]) == set(CLASSES)
    assert task_class["criteria"]["question"]["examples"]  # descriptions carry examples
    needs = body["questions"]["needs_detail"]
    assert needs["type"] == "noul" and set(needs["criteria"]) == {"true", "false"}


def test_judge_jev_parses_a_good_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=FIXTURE)

    j = judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(handler))
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert (j.task_class, j.confidence, j.needs_detail, j.source) == ("simple_change", 0.81, 0.07, "jev")
    assert j.probabilities["simple_change"] == 0.86
    assert j.usage.input_tokens == 412 and j.usage.output_tokens == 3


@pytest.mark.parametrize(("status", "reason"), [(401, "http 401"), (422, "http 422"), (429, "http 429"),
                                                 (529, "http 529"), (500, "http 500")])
def test_http_errors_are_jev_errors_without_the_key(status, reason):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, text=f"nope {SECRET}")

    with pytest.raises(JevError) as info:
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(handler))
    assert info.value.reason == reason and SECRET not in str(info.value)
    assert calls == [1]  # exactly one attempt, no retries


def test_timeout_and_network_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(JevError, match="timeout"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(slow))
    with pytest.raises(JevError, match="network"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(down))


@pytest.mark.parametrize(
    "body",
    [
        {},  # no answers
        {"answers": {"needs_detail": {"noul": 0.1}}},  # no task_class
        {"answers": {"task_class": {"choice": "invent", "probabilities": {}, "confidence": 0.9},
                     "needs_detail": {"noul": 0.1}}},  # not one of the classes
        {"answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 2},
                     "needs_detail": {"noul": 0.1}}},  # out of range
        {"answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 0.9}}},  # no noul
        "not json",
        # Malformed usage fields
        {"model": "jev-latest", "answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 0.9},
                                            "needs_detail": {"noul": 0.1}}, "usage": {"input_tokens": "abc", "output_tokens": 3}},  # string token
        {"model": "jev-latest", "answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 0.9},
                                            "needs_detail": {"noul": 0.1}}, "usage": {"input_tokens": None, "output_tokens": 3}},  # null token
        {"model": "jev-latest", "answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 0.9},
                                            "needs_detail": {"noul": 0.1}}, "usage": {"input_tokens": -1, "output_tokens": 3}},  # negative token
        {"model": "jev-latest", "answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 0.9},
                                            "needs_detail": {"noul": 0.1}}, "usage": {"input_tokens": True, "output_tokens": 3}},  # bool token
    ],
)
def test_malformed_bodies(body):
    def handler(request):
        return httpx.Response(200, json=body) if not isinstance(body, str) else httpx.Response(200, text=body)

    with pytest.raises(JevError, match="malformed"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(handler))


def test_missing_key_never_calls_out():
    def handler(request):
        raise AssertionError("must not be called")

    with pytest.raises(JevError, match="missing key"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ={}, transport=transport(handler))


def test_ping_sends_a_tiny_choice():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"answers": {"ping": {"type": "choice", "choice": "yes",
                                          "probabilities": {"yes": 1.0, "no": 0.0}, "confidence": 1.0}}})

    ping_jev(SPEC, "jev-latest", timeout_s=5, environ=ENV, transport=transport(handler))
    assert set(seen["body"]["questions"]) == {"ping"}


def test_a_request_that_cant_be_built_is_a_jev_error_without_the_key():
    key = "ts-“smart”-quote"
    with pytest.raises(JevError) as info:
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ={"TYPESAFE_API_KEY": key},
                  transport=transport(lambda r: httpx.Response(200, json=FIXTURE)))
    assert info.value.reason == "request failed" and str(info.value) == "request failed"
    assert key not in str(info.value) and "“" not in str(info.value)
    assert info.value.__suppress_context__


def test_a_non_string_choice_is_malformed():
    body = json.loads(json.dumps(FIXTURE))
    body["answers"]["task_class"]["choice"] = ["question"]
    with pytest.raises(JevError) as info:
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV,
                  transport=transport(lambda r: httpx.Response(200, json=body)))
    assert info.value.reason == "malformed"


OPENROUTER_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jev_openrouter_ok.json").read_text())
OPENROUTER_SPEC = BUILTIN_PROVIDERS["openrouter_decisions"]
OPENROUTER_ENV = {"OPENROUTER_API_KEY": "sk-or-TESTSECRET0123456789"}


def test_openrouter_decisions_posts_to_openrouters_systemone_endpoint_and_reads_its_cost():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json=OPENROUTER_FIXTURE)

    judgement = judge_jev(
        OPENROUTER_SPEC, "typesafe/jev-1.13", STATE, timeout_s=5, environ=OPENROUTER_ENV, transport=transport(handler)
    )
    assert seen["url"] == "https://openrouter.ai/api/v1/systemone"
    assert seen["auth"] == "Bearer sk-or-TESTSECRET0123456789"
    assert judgement.task_class == "simple_change" and judgement.source == "jev"
    assert judgement.usage.cost_usd == pytest.approx(0.0000173)


def test_a_typesafe_response_without_a_cost_leaves_it_unknown():
    judgement = judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV,
                          transport=transport(lambda r: httpx.Response(200, json=FIXTURE)))
    assert judgement.usage.cost_usd is None


@pytest.mark.parametrize("cost", ["lots", -1, True])
def test_an_unreadable_reported_cost_is_unknown(cost):
    # Cost is metadata: one that can't be read doesn't throw away a valid decision.
    body = {**OPENROUTER_FIXTURE, "usage": {"input_tokens": 1, "output_tokens": 1, "cost": cost}}
    judgement = judge_jev(OPENROUTER_SPEC, "typesafe/jev-1.13", STATE, timeout_s=5, environ=OPENROUTER_ENV,
                          transport=transport(lambda r: httpx.Response(200, json=body)))
    assert judgement.task_class == "simple_change" and judgement.source == "jev"
    assert judgement.usage.cost_usd is None
    assert (judgement.usage.input_tokens, judgement.usage.output_tokens) == (1, 1)
