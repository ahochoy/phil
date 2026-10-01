import dataclasses

import pytest

from phil.agents.factory import READ_TOOLS, _call_budget_middleware, build_agent
from phil.agents.registry import get_spec
from phil.contracts.routing import Answer
from tests.agents.test_model_retry import ScriptedChatModel, tool_call


def test_answer_spec_is_light_read_only_and_capped():
    spec = get_spec("answer")
    assert (spec.harness, spec.role, spec.read_only_shell, spec.max_model_calls) == ("light", "answerer", True, 12)
    assert spec.writes_files is False and spec.tools == ("shell",)


def test_light_agent_has_only_read_tools_and_no_task_tool(tmp_path, monkeypatch):
    # Build with a fake chat model so no provider or network is involved.
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: GenericFakeChatModel(messages=iter([])))
    agent = build_agent(get_spec("answer"), "openrouter:x", tmp_path, [])
    assert set(READ_TOOLS) <= _tool_names(agent)
    assert not {"write_file", "edit_file", "delete", "task", "execute"} & _tool_names(agent)


def _tool_names(agent) -> set[str]:
    tools_node = agent.nodes.get("tools")
    bound = getattr(getattr(tools_node, "bound", None), "tools_by_name", {}) if tools_node else {}
    return set(bound)


def test_light_harness_refuses_a_spec_that_writes_files(tmp_path):
    spec = dataclasses.replace(get_spec("answer"), writes_files=True)
    with pytest.raises(ValueError, match="writes_files"):
        build_agent(spec, "openrouter:x", tmp_path, [])


def test_light_harness_needs_a_workdir():
    with pytest.raises(ValueError, match="workdir"):
        build_agent(get_spec("answer"), "openrouter:x", None, [])


def test_call_budget_forces_an_answer_on_the_last_call():
    mw = _call_budget_middleware(3)
    seen = []

    class Req:
        def __init__(self, n):
            self.state = {"messages": [type("AI", (), {"type": "ai"})()] * n}
            self.tools = ["ls", "read_file"]
            self.system_prompt = "base"

        def override(self, **kw):
            r = Req(len(self.state["messages"]))
            r.tools, r.system_prompt = kw.get("tools", self.tools), kw.get("system_prompt", self.system_prompt)
            return r

    for n in (0, 1, 2):
        mw.wrap_model_call(Req(n), lambda r: seen.append((r.tools, r.system_prompt)))
    assert seen[0][0] == ["ls", "read_file"] and seen[1][0] == ["ls", "read_file"]
    assert seen[2][0] == [] and "Answer now" in seen[2][1]


class ToolRecordingModel(ScriptedChatModel):
    """Records the tool names bound for each model call, and the system prompt it was sent."""

    bound: list[list[str]] = []
    systems: list[str] = []

    def model_post_init(self, _context) -> None:
        super().model_post_init(_context)
        self.bound, self.systems = [], []

    def bind_tools(self, tools, **kwargs):
        self.bound.append([getattr(t, "name", None) or t.get("name") for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.systems.append(messages[0].text if messages and messages[0].type == "system" else "")
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_the_capped_last_call_keeps_only_the_answer_tool_and_still_answers(tmp_path, monkeypatch):
    # Through the real create_agent graph: once the budget is spent, the model sees only the
    # structured-output tool (ToolStrategy adds it after the middleware's tools=[]), and its
    # Answer becomes the structured response.
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    spec = dataclasses.replace(get_spec("answer"), max_model_calls=3)
    model = ToolRecordingModel(
        script=[
            tool_call("ls", {"path": "/"}, "c1"),
            tool_call("read_file", {"file_path": "/calc.py"}, "c2"),
            tool_call("Answer", {"text": "add sums two numbers.", "files": ["calc.py"]}, "c3"),
        ]
    )
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    agent = build_agent(spec, "openrouter:x", tmp_path, [])
    result = agent.invoke({"messages": [{"role": "user", "content": "what does add do?"}]})
    assert result["structured_response"] == Answer(text="add sums two numbers.", files=["calc.py"])
    assert len(model.bound) == 3
    assert set(READ_TOOLS) | {"Answer"} <= set(model.bound[0]) and model.bound[0] == model.bound[1]
    assert model.bound[2] == ["Answer"]
    assert "Answer now" in model.systems[2] and "Answer now" not in model.systems[1]


def test_an_uncapped_light_agent_keeps_its_tools(tmp_path, monkeypatch):
    spec = dataclasses.replace(get_spec("answer"), max_model_calls=None)
    model = ToolRecordingModel(
        script=[
            tool_call("ls", {"path": "/"}, "c1"),
            tool_call("Answer", {"text": "Nothing here.", "files": []}, "c2"),
        ]
    )
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    agent = build_agent(spec, "openrouter:x", tmp_path, [])
    result = agent.invoke({"messages": [{"role": "user", "content": "anything?"}]})
    assert result["structured_response"].text == "Nothing here."
    assert model.bound[1] == model.bound[0] and "ls" in model.bound[1]


def test_a_light_agent_writes_nothing_into_the_repo(tmp_path, monkeypatch):
    # deepagents offloads large tool results to the backend; for a read-only agent rooted at the
    # repo that would be a write, so the light harness turns eviction off.
    (tmp_path / "big.txt").write_text(("line " + "y" * 200 + "\n") * 2000)  # grep output well over the limit
    model = ToolRecordingModel(
        script=[
            tool_call("grep", {"pattern": "line", "path": "/"}, "c1"),
            tool_call("Answer", {"text": "Lots of lines.", "files": ["big.txt"]}, "c2"),
        ]
    )
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    agent = build_agent(get_spec("answer"), "openrouter:x", tmp_path, [])
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    agent.invoke({"messages": [{"role": "user", "content": "x" * 300_000}]})
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
