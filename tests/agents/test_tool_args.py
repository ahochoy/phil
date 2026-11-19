"""A file tool called with `path` instead of `file_path` still runs (live: Ollama's flint sent
`path` to read_file and write_file and spent a whole run's budget on the validation errors)."""

import dataclasses

import pytest

from phil.agents.registry import get_spec
from phil.agents.tool_args import ToolCallRepair, aliased_args, repair_arguments, repaired_history
from tests.agents.test_architect_cap import PLAN_ARGS, MessageRecordingModel
from tests.agents.test_model_retry import tool_call


def test_path_becomes_file_path_for_a_tool_that_takes_file_path():
    assert aliased_args({"path": "/a.py", "content": "x"}, {"file_path", "content"}) == {
        "file_path": "/a.py",
        "content": "x",
    }


def test_a_tool_that_takes_path_keeps_it():
    assert aliased_args({"path": "/"}, {"path"}) is None


def test_file_path_wins_when_both_are_sent():
    assert aliased_args({"file_path": "/a.py", "path": "/b.py"}, {"file_path"}) is None


def test_a_call_without_path_is_left_alone():
    assert aliased_args({"pattern": "*.py"}, {"file_path"}) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1,}', {"a": 1}),
        ('{"path": "a.py",', {"path": "a.py"}),
        ('{"x": "unterminated', {"x": "unterminated"}),
        ("not json", {}),
        ("[1,2]", {}),
    ],
)
def test_repair_arguments(raw, expected):
    assert repair_arguments(raw) == expected


def _broken_history():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    broken = AIMessage(
        content="",
        invalid_tool_calls=[
            {"id": "c1", "name": "read_file", "args": '{"path": "a.py",', "error": "bad", "type": "invalid_tool_call"}
        ],
    )
    result = ToolMessage(content="Error: invalid arguments", tool_call_id="c1")
    return [HumanMessage(content="go"), broken, result]


def test_invalid_tool_call_becomes_valid_and_keeps_its_result():
    messages = _broken_history()
    repaired = repaired_history(messages)
    assert repaired is not None
    ai = repaired[1]
    assert ai.tool_calls[0]["args"] == {"path": "a.py"}
    assert ai.tool_calls[0]["id"] == "c1"
    assert ai.tool_calls[0]["name"] == "read_file"
    assert not ai.invalid_tool_calls
    assert repaired[2] is messages[2]
    assert messages[1].invalid_tool_calls  # the original is untouched


def test_a_bad_additional_kwargs_arguments_string_is_repaired():
    from langchain_core.messages import AIMessage

    raw = {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py",'}}
    message = AIMessage(content="", additional_kwargs={"tool_calls": [raw]})
    repaired = repaired_history([message])
    assert repaired is not None
    assert repaired[0].additional_kwargs["tool_calls"][0]["function"]["arguments"] == '{"path": "a.py"}'
    assert raw["function"]["arguments"] == '{"path": "a.py",'


def test_valid_history_is_left_alone():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages = [
        HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "a.py"}}]),
        ToolMessage(content="ok", tool_call_id="c1"),
    ]
    assert repaired_history(messages) is None


def test_middleware_sends_the_repaired_history():
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    messages = _broken_history()
    request = ModelRequest(model=FakeListChatModel(responses=["x"]), messages=messages, tools=[])
    seen = []
    ToolCallRepair().wrap_model_call(request, lambda r: seen.append(r) or "response")
    assert seen[0].messages[1].tool_calls[0]["args"] == {"path": "a.py"}
    assert request.messages is messages  # the request itself is not changed


def test_middleware_passes_a_valid_request_through_unchanged():
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import HumanMessage

    request = ModelRequest(model=FakeListChatModel(responses=["x"]), messages=[HumanMessage(content="hi")], tools=[])
    seen = []
    ToolCallRepair().wrap_model_call(request, lambda r: seen.append(r) or "response")
    assert seen == [request]


def test_a_light_agent_reads_a_file_named_with_path(tmp_path, monkeypatch):
    from phil.agents.factory import build_agent

    (tmp_path / "page.tsx").write_text("export default function Home() { return <main /> }\n")
    spec = dataclasses.replace(get_spec("architect"), max_model_calls=5)
    model = MessageRecordingModel(script=[
        tool_call("read_file", {"path": "/page.tsx"}, "c1"),
        tool_call("Plan", PLAN_ARGS, "c2"),
    ])
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    result = build_agent(spec, "ollama:x", tmp_path, []).invoke({"messages": [{"role": "user", "content": "plan it"}]})
    [read] = [m for m in result["messages"] if m.type == "tool" and m.tool_call_id == "c1"]
    assert "export default function Home" in read.text


def test_a_light_agent_writes_a_file_named_with_path(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage

    from phil.agents.factory import build_agent
    from tests.agents.test_light_harness import ToolRecordingModel
    from tests.agents.test_model_retry import usage

    spec = dataclasses.replace(get_spec("quick_implementer"), max_model_calls=5)
    done = [usage(AIMessage(content="done")) for _ in range(3)]  # no answer: two reminders, then the end
    model = ToolRecordingModel(script=[tool_call("write_file", {"path": "/greet.py", "content": "x = 1\n"}, "c1"), *done])
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    build_agent(spec, "ollama:x", tmp_path, []).invoke({"messages": [{"role": "user", "content": "go"}]})
    assert (tmp_path / "greet.py").read_text() == "x = 1\n"
