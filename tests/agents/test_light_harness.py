import asyncio
import dataclasses

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from phil.agents.factory import ANSWER_NOW, BUDGET_GRACE_CALLS, READ_TOOLS, _call_budget_middleware, build_agent
from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.registry import ANSWER_MAX_MODEL_CALLS, get_spec
from phil.contracts.routing import Answer, AnswerInput
from phil.packets import build_packet
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


class Req:
    def __init__(self, n, system_message=SystemMessage(content="base")):
        self.state = {"messages": [AIMessage(content="")] * n}
        self.tools = ["ls", "read_file"]
        self.system_message = system_message

    def override(self, **kw):
        r = Req(len(self.state["messages"]), kw.get("system_message", self.system_message))
        r.tools = kw.get("tools", self.tools)
        return r


def test_call_budget_forces_an_answer_on_the_last_call():
    mw = _call_budget_middleware(3)
    seen = []
    for n in (0, 1, 2):
        mw.wrap_model_call(Req(n), lambda r: seen.append((r.tools, r.system_message.text)))
    assert seen[0] == (["ls", "read_file"], "base") and seen[1] == (["ls", "read_file"], "base")
    assert seen[2][0] == [] and seen[2][1].startswith("base\n\n") and "Answer now" in seen[2][1]


def test_call_budget_without_a_system_prompt_adds_only_the_instruction():
    seen = []
    _call_budget_middleware(1).wrap_model_call(Req(0, None), lambda r: seen.append(r.system_message.text))
    assert seen == [ANSWER_NOW]


def test_a_string_system_prompt_stays_a_plain_string():
    # OpenAI-compatible servers can reject list content in a system message.
    seen = []
    _call_budget_middleware(1).wrap_model_call(Req(0), lambda r: seen.append(r.system_message.content))
    assert seen == [f"base\n\n{ANSWER_NOW}"]


def test_a_block_system_prompt_keeps_its_blocks():
    blocks = [{"type": "text", "text": "base"}, {"type": "text", "text": "more"}]
    seen = []
    _call_budget_middleware(1).wrap_model_call(
        Req(0, SystemMessage(content=blocks)), lambda r: seen.append(r.system_message.content)
    )
    [content] = seen
    assert isinstance(content, list) and content[:2] == blocks
    assert content[2]["type"] == "text" and content[2]["text"] == f"\n\n{ANSWER_NOW}"


def test_no_system_prompt_gives_a_plain_string():
    seen = []
    _call_budget_middleware(1).wrap_model_call(Req(0, None), lambda r: seen.append(r.system_message.content))
    assert seen == [ANSWER_NOW]


def test_call_budget_async_twin_caps_too():
    seen = []

    async def handler(r):
        seen.append(r.tools)

    asyncio.run(_call_budget_middleware(3).awrap_model_call(Req(2), handler))
    assert seen == [[]]


def _invalid_answers(n: int) -> list:
    # Missing the required `text`: ToolStrategy sends the error back and the model tries again.
    return [tool_call("Answer", {"files": []}, f"c{i}") for i in range(n)]


def test_the_hard_stop_ends_a_model_that_never_answers_validly(tmp_path, monkeypatch):
    spec = dataclasses.replace(get_spec("answer"), max_model_calls=3)
    model = ToolRecordingModel(script=[tool_call("ls", {"path": "/"}, "l1"), *_invalid_answers(50)])
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    agent = build_agent(spec, "openrouter:x", tmp_path, [])
    result = agent.invoke({"messages": [{"role": "user", "content": "what?"}]})
    assert result.get("structured_response") is None
    assert len(model.received) == 3 + BUDGET_GRACE_CALLS == 5


def test_the_hard_stop_also_holds_for_an_async_invoke(tmp_path, monkeypatch):
    spec = dataclasses.replace(get_spec("answer"), max_model_calls=3)
    model = ToolRecordingModel(script=[tool_call("ls", {"path": "/"}, "l1"), *_invalid_answers(50)])
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    agent = build_agent(spec, "openrouter:x", tmp_path, [])
    result = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": "what?"}]}))
    assert result.get("structured_response") is None
    assert len(model.received) == 5
    assert model.bound[2:] == [["Answer"]] * 3  # every call from the cap on has only the answer tool


def test_invoke_agent_bounds_a_never_answering_answerer(tmp_path, monkeypatch, config, conn):
    # Each attempt stops at max_model_calls + grace; invoke_agent records the rejection and makes
    # its single contract retry, so one ask is bounded at 2 * (12 + 2) model calls.
    model = ToolRecordingModel(script=_invalid_answers(100))
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    ctx = AgentContext(config=config, conn=conn, layer="chat", workdir=tmp_path, factory=build_agent)
    packet = build_packet("answerer", AnswerInput(question="what?"), budget_tokens=4000)
    with pytest.raises(ContractViolation):
        invoke_agent(get_spec("answer"), packet, ctx, node="answer")
    assert len(model.received) == 2 * (ANSWER_MAX_MODEL_CALLS + BUDGET_GRACE_CALLS) == 28


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
