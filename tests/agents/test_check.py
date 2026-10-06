import httpx
from langchain_core.messages import AIMessage

from phil.agents.check import CHECK_WORD, CheckResult, ModelCheck, check_models, check_targets, unused_tiers
from phil.agents.fake import ScriptedAgentFactory
from phil.config import ROLES, PhilConfig
from tests.agents.test_model_retry import ScriptedChatModel, real_factory, tool_call

OK = ModelCheck(ok=True, echo=CHECK_WORD)


class TextOnlyFactory:
    """An agent that answers in prose and never returns the structured output."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def __call__(self, spec, model, workdir, tools, *, timeout_s=180, provider=None):
        return self

    def invoke(self, payload, config=None):
        self.calls += 1
        return {"messages": [AIMessage(content=self.text)]}


def test_one_call_per_tier_when_the_models_differ(tmp_path):
    config = PhilConfig(models={"high": "ollama:big", "low": "ollama:small"})
    factory = ScriptedAgentFactory({"model_check": [OK, OK]})
    results = check_models(config, factory=factory, repo_root=tmp_path)
    assert [(r.label, r.model, r.ok) for r in results] == [("high", "ollama:big", True), ("low", "ollama:small", True)]
    assert factory.remaining() == {"model_check": 0}
    assert all(r.detail == "" and r.seconds >= 0 for r in results)


def test_one_call_when_the_tiers_share_a_model(tmp_path):
    config = PhilConfig(models={"high": "ollama:same", "low": "ollama:same"})
    factory = ScriptedAgentFactory({"model_check": [OK]})
    [result] = check_models(config, factory=factory, repo_root=tmp_path)
    assert (result.label, result.model, result.ok) == ("high, low", "ollama:same", True)
    assert factory.remaining() == {"model_check": 0}


def test_role_keys_that_override_their_tier_are_checked_too(tmp_path):
    config = PhilConfig(
        models={"high": "ollama:big", "low": "ollama:small", "classifier": "ollama:tiny", "critic": "ollama:judge"}
    )
    factory = ScriptedAgentFactory({"model_check": [OK, OK, OK, OK]})
    results = check_models(config, factory=factory, repo_root=tmp_path)
    assert [(r.label, r.model) for r in results] == [
        ("high", "ollama:big"),
        ("low", "ollama:small"),
        ("classifier", "ollama:tiny"),  # [models] classifier is the classifier tier, not a role override
        ("role:critic", "ollama:judge"),
    ]
    assert factory.remaining() == {"model_check": 0}


def test_no_models_gives_no_results(tmp_path):
    assert check_models(PhilConfig(), factory=ScriptedAgentFactory({}), repo_root=tmp_path) == []


def test_text_instead_of_structured_output_fails_with_an_excerpt(tmp_path):
    text = "I'd be happy to help! " + "x" * 200
    factory = TextOnlyFactory(text)
    [result] = check_models(PhilConfig(models={"low": "ollama:chatty"}), factory=factory, repo_root=tmp_path)
    assert not result.ok
    assert result.detail == f'returned text instead of the required structured output: "{text[:120]}"'
    assert factory.calls == 1  # no contract retry


def test_an_error_is_reported_once_without_retries(tmp_path):
    factory = ScriptedAgentFactory({"model_check": [TimeoutError("read timed out"), OK]})
    [result] = check_models(PhilConfig(models={"low": "ollama:slow"}), factory=factory, repo_root=tmp_path)
    assert not result.ok
    assert result.detail == "TimeoutError: read timed out"
    assert factory.remaining() == {"model_check": 1}  # tried once


def test_a_wrong_echo_fails(tmp_path):
    factory = ScriptedAgentFactory({"model_check": [ModelCheck(ok=True, echo="banana")]})
    [result] = check_models(PhilConfig(models={"low": "ollama:odd"}), factory=factory, repo_root=tmp_path)
    assert not result.ok
    assert result.detail == f'echoed "banana" instead of "{CHECK_WORD}"'


def test_an_unknown_provider_is_reported(tmp_path):
    [result] = check_models(PhilConfig(models={"low": "nowhere:m"}), factory=ScriptedAgentFactory({}), repo_root=tmp_path)
    assert not result.ok
    assert result.detail.startswith('Unknown provider "nowhere" in low model "nowhere:m".')


def test_the_real_lean_agent_path_succeeds(tmp_path, monkeypatch):
    model = ScriptedChatModel(script=[tool_call("ModelCheck", {"ok": True, "echo": CHECK_WORD}, "c1")])
    [result] = check_models(
        PhilConfig(models={"low": "ollama:m"}), factory=real_factory(model, monkeypatch), repo_root=tmp_path
    )
    assert result == CheckResult(label="low", model="ollama:m", ok=True, seconds=result.seconds, detail="")


def test_the_real_lean_agent_path_reports_prose(tmp_path, monkeypatch):
    model = ScriptedChatModel(script=[AIMessage(content="Sure, pineapple!")])
    [result] = check_models(
        PhilConfig(models={"low": "ollama:m"}), factory=real_factory(model, monkeypatch), repo_root=tmp_path
    )
    assert result.detail == 'returned text instead of the required structured output: "Sure, pineapple!"'
    assert len(model.received) == 1  # the text answer ends the check; LangChain doesn't ask again


def test_the_real_lean_agent_path_does_not_retry_a_transient_error(tmp_path, monkeypatch):
    model = ScriptedChatModel(script=[TimeoutError("slow"), tool_call("ModelCheck", OK.model_dump(), "c1")])
    [result] = check_models(
        PhilConfig(models={"low": "ollama:m"}), factory=real_factory(model, monkeypatch), repo_root=tmp_path
    )
    assert not result.ok
    assert len(model.received) == 1


def test_a_missing_key_names_the_labels_that_use_the_model(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = PhilConfig(models={"high": "openai:gpt-x", "low": "openai:gpt-x"})
    [result] = check_models(config, factory=ScriptedAgentFactory({}), repo_root=tmp_path)
    assert not result.ok
    assert result.detail == "openai needs OPENAI_API_KEY (used by high, low)."


def test_the_precheck_passes_with_a_store_only_key(tmp_path, monkeypatch):
    from phil.key_store import set_key

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    config = PhilConfig(models={"low": "openai:gpt-x"})
    factory = ScriptedAgentFactory({"model_check": [OK]})
    [result] = check_models(config, factory=factory, repo_root=tmp_path)
    assert result.ok
    assert factory.remaining() == {"model_check": 0}


def test_only_models_some_role_resolves_to_are_checked(tmp_path):
    # A global high/low under a legacy repo config that sets every role: the tiers are unused,
    # except classifier, whose [models] key is the classifier tier itself (not a role override),
    # so the classifier role resolving through it keeps that tier used.
    models = {"high": "ollama:big", "low": "ollama:small"} | {role: "ollama:legacy" for role in ROLES}
    config = PhilConfig(models=models)
    assert check_targets(config) == [
        ("ollama:legacy", ["classifier"] + [f"role:{role}" for role in ROLES if role != "classifier"]),
    ]
    factory = ScriptedAgentFactory({"model_check": [OK]})
    [result] = check_models(config, factory=factory, repo_root=tmp_path)
    assert (result.model, result.ok) == ("ollama:legacy", True)
    assert factory.remaining() == {"model_check": 0}
    assert unused_tiers(config) == [("high", "ollama:big"), ("low", "ollama:small")]


def test_the_classifier_tier_is_not_unused_when_the_role_resolves_through_it(tmp_path):
    # Setting models["classifier"] sets the classifier tier directly: the classifier role
    # resolves through it (label "classifier", not "role:classifier"), so the tier is in use and
    # is never reported as unused, even though no OTHER role is tier-remapped onto it.
    config = PhilConfig(models={"high": "ollama:big", "low": "ollama:small", "classifier": "ollama:tiny"})
    assert [model for model, _ in check_targets(config)] == ["ollama:big", "ollama:small", "ollama:tiny"]
    assert unused_tiers(config) == []


def test_a_tier_remapped_role_labels_its_new_tier(tmp_path):
    config = PhilConfig(
        models={"high": "ollama:big", "low": "ollama:small", "classifier": "ollama:tiny"},
        tiers={"tester": "classifier"},
    )
    assert check_targets(config) == [
        ("ollama:big", ["high"]), ("ollama:small", ["low"]),
        # tester's remap onto the classifier tier, and the classifier role itself, share one label.
        ("ollama:tiny", ["classifier"]),
    ]
    assert unused_tiers(config) == []


def test_check_pings_a_typesafe_classifier(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    ok = {"answers": {"ping": {"type": "choice", "choice": "yes", "probabilities": {"yes": 1.0}, "confidence": 1.0}}}
    config = PhilConfig(models={"classifier": "typesafe:jev-latest"})
    results = check_models(config, repo_root=tmp_path,
                           jev_transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ok)))
    [result] = [r for r in results if r.model == "typesafe:jev-latest"]
    assert result.ok and result.label == "classifier"


def test_check_reports_a_failing_typesafe_classifier_plainly(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    config = PhilConfig(models={"classifier": "typesafe:jev-latest"})
    results = check_models(config, repo_root=tmp_path,
                           jev_transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    [result] = [r for r in results if r.model == "typesafe:jev-latest"]
    assert not result.ok and result.detail == "TypeSafe refused the request (http 401): check TYPESAFE_API_KEY."


def test_check_pings_an_openrouter_decisions_classifier_at_openrouter(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    seen = {}
    ok = {"answers": {"ping": {"type": "choice", "choice": "yes", "probabilities": {"yes": 1.0}, "confidence": 1.0}},
          "usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.0}}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=ok)

    config = PhilConfig(models={"classifier": "openrouter_decisions:typesafe/jev-1.13"})
    results = check_models(config, repo_root=tmp_path, jev_transport=httpx.MockTransport(handler))
    [result] = [r for r in results if r.model == "openrouter_decisions:typesafe/jev-1.13"]
    assert result.ok and seen["url"] == "https://openrouter.ai/api/v1/systemone"


def test_a_refused_openrouter_decision_call_names_openrouter_and_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    config = PhilConfig(models={"classifier": "openrouter_decisions:typesafe/jev-1.13"})
    results = check_models(config, repo_root=tmp_path,
                           jev_transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    [result] = [r for r in results if r.model == "openrouter_decisions:typesafe/jev-1.13"]
    assert result.detail == "OpenRouter refused the request (http 401): check OPENROUTER_API_KEY."
