"""A file tool called with `path` instead of `file_path` still runs (live: Ollama's flint sent
`path` to read_file and write_file and spent a whole run's budget on the validation errors)."""

import dataclasses

from phil.agents.registry import get_spec
from phil.agents.tool_args import aliased_args
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
