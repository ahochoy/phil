import pytest

from phil.agents.fake import FakeAgentFactory, FakeMessage
from phil.agents.registry import get_spec
from phil.agents.usage import Usage, extract_usage


def test_extract_usage_sums_messages():
    messages = [
        FakeMessage(usage_metadata={"input_tokens": 100, "output_tokens": 10}, response_metadata={"cost": 0.01}),
        object(),
        FakeMessage(usage_metadata={"input_tokens": 50, "output_tokens": 5}),
    ]
    assert extract_usage(messages) == Usage(150, 15, 0.01)


def test_extract_usage_of_nothing_is_zero():
    assert extract_usage([]) == Usage(0, 0, 0.0)


def test_fake_factory_returns_outputs_in_order():
    factory = FakeAgentFactory(["first", "second"], usage=(10, 2, 0.5))
    agent = factory(get_spec("critic"), "model-x", None, [])
    first = agent.invoke({"messages": [{"role": "user", "content": "a"}]})
    second = agent.invoke({"messages": []})
    assert (first["structured_response"], second["structured_response"]) == ("first", "second")
    assert extract_usage(first["messages"]) == Usage(10, 2, 0.5)
    assert factory.built == [("critic", "model-x")]
    assert agent.calls[0]["messages"][0]["content"] == "a"


def test_fake_agent_raises_queued_exceptions():
    agent = FakeAgentFactory([RuntimeError("boom")])(get_spec("critic"), "m", None, [])
    with pytest.raises(RuntimeError, match="boom"):
        agent.invoke({"messages": []})


def test_fake_factory_records_tool_names():
    def run_shell(command: str) -> str:
        return command

    factory = FakeAgentFactory([])
    factory(get_spec("implementer"), "m", None, [run_shell])
    assert factory.tools_seen == [["run_shell"]]
