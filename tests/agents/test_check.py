from langchain_core.messages import AIMessage

from phil.agents.check import CHECK_WORD, CheckResult, ModelCheck, check_models
from phil.agents.fake import ScriptedAgentFactory
from phil.config import PhilConfig
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
        ("classifier", "ollama:tiny"),
        ("role:critic", "ollama:judge"),
    ]


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
