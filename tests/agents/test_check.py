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
        ("role:critic", "ollama:judge"),
        ("role:classifier", "ollama:tiny"),  # the classifier role's own key, not the bare tier
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
    # A global high/low under a legacy repo config that sets every role: the tiers are unused.
    # The dict also sets models["classifier"] (since "classifier" is one of ROLES), which is the
    # same key as the classifier tier bucket, so that tier shows up as unused too.
    models = {"high": "ollama:big", "low": "ollama:small"} | {role: "ollama:legacy" for role in ROLES}
    config = PhilConfig(models=models)
    assert check_targets(config) == [("ollama:legacy", [f"role:{role}" for role in ROLES])]
    factory = ScriptedAgentFactory({"model_check": [OK]})
    [result] = check_models(config, factory=factory, repo_root=tmp_path)
    assert (result.model, result.ok) == ("ollama:legacy", True)
    assert factory.remaining() == {"model_check": 0}
    assert unused_tiers(config) == [
        ("high", "ollama:big"), ("low", "ollama:small"), ("classifier", "ollama:legacy"),
    ]


def test_a_tier_no_role_maps_to_is_unused(tmp_path):
    # Setting models["classifier"] sets both the classifier tier's bucket model and (since
    # "classifier" is also a role) the classifier role's own model, so both appear: the role's
    # own key is checked directly (label "role:classifier"), and the bare tier is still reported
    # as unused (no OTHER role is tier-remapped onto it).
    config = PhilConfig(models={"high": "ollama:big", "low": "ollama:small", "classifier": "ollama:tiny"})
    assert [model for model, _ in check_targets(config)] == ["ollama:big", "ollama:small", "ollama:tiny"]
    assert unused_tiers(config) == [("classifier", "ollama:tiny")]


def test_a_tier_remapped_role_labels_its_new_tier(tmp_path):
    config = PhilConfig(
        models={"high": "ollama:big", "low": "ollama:small", "classifier": "ollama:tiny"},
        tiers={"tester": "classifier"},
    )
    assert check_targets(config) == [
        ("ollama:big", ["high"]), ("ollama:small", ["low"]),
        ("ollama:tiny", ["classifier", "role:classifier"]),  # tester's remap, and the classifier role itself
    ]
    assert unused_tiers(config) == []
