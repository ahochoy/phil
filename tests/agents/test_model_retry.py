"""Per-model-call retries (phil.agents.model_retry): a transient error on one model call of an
agent retries that call only — earlier model calls and tool calls are not repeated."""

import threading
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from phil.agents.factory import build_agent
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.model_retry import TRACKER_KEY, ModelRetryTracker, PhilModelRetryMiddleware, model_call_retried
from phil.agents.registry import get_spec
from phil.contracts import ArchitectInput, Goal
from phil.packets import build_packet
from tests.agents.conftest import critique


class HTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


class ScriptedChatModel(BaseChatModel):
    """Answers each call with the next scripted item: an AIMessage, or an exception to raise.
    Counts every call it receives (including the failed ones)."""

    script: list[Any]
    received: list[int] = []

    def model_post_init(self, _context: Any) -> None:
        self._lock = threading.Lock()
        self.received = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        with self._lock:
            self.received.append(len(messages))
            if not self.script:
                raise AssertionError("no scripted model output left")
            item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


def usage(message: AIMessage) -> AIMessage:
    message.usage_metadata = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    return message


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return usage(AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}]))


ECHOES: list[str] = []


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    ECHOES.append(text)
    return text


def run(agent, sleep) -> tuple[dict, ModelRetryTracker]:
    tracker = ModelRetryTracker(sleep=sleep)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"configurable": {TRACKER_KEY: tracker}}
    )
    return result, tracker


def test_a_transient_error_on_the_second_model_call_retries_only_that_call():
    ECHOES.clear()
    model = ScriptedChatModel(
        script=[tool_call("echo", {"text": "once"}, "c1"), HTTPError(503), usage(AIMessage(content="done"))]
    )
    agent = create_agent(model, tools=[echo], middleware=[PhilModelRetryMiddleware()])
    delays: list[float] = []
    result, tracker = run(agent, delays.append)
    assert result["messages"][-1].content == "done"
    assert len(model.received) == 3  # call 1, the failed call 2, its one retry
    assert model.received[2] == model.received[1] > model.received[0]
    assert ECHOES == ["once"]  # the tool ran once: the agent was not re-run
    assert tracker.retries == 1
    assert delays == [1.0]


def test_a_timeout_is_retried_at_most_once_then_propagates_marked():
    model = ScriptedChatModel(script=[TimeoutError(), TimeoutError(), usage(AIMessage(content="never"))])
    agent = create_agent(model, tools=[], middleware=[PhilModelRetryMiddleware()])
    delays: list[float] = []
    with pytest.raises(TimeoutError) as excinfo:
        run(agent, delays.append)
    assert len(model.received) == 2
    assert delays == [1.0]
    assert model_call_retried(excinfo.value)


def test_other_transient_errors_get_two_retries():
    model = ScriptedChatModel(script=[HTTPError(429), HTTPError(429), HTTPError(429), usage(AIMessage(content="x"))])
    agent = create_agent(model, tools=[], middleware=[PhilModelRetryMiddleware()])
    delays: list[float] = []
    with pytest.raises(HTTPError):
        run(agent, delays.append)
    assert len(model.received) == 3
    assert delays == [1.0, 2.0]


def test_a_non_transient_error_propagates_without_retry():
    model = ScriptedChatModel(script=[HTTPError(401), usage(AIMessage(content="never"))])
    agent = create_agent(model, tools=[], middleware=[PhilModelRetryMiddleware()])
    delays: list[float] = []
    with pytest.raises(HTTPError):
        run(agent, delays.append)
    assert len(model.received) == 1
    assert delays == []


def architect_packet():
    return build_packet("architect", ArchitectInput(goal=Goal(objective="Add subtract")), budget_tokens=4000)


PLAN_ARGS = {
    "keyword": "CALC",
    "description": "Add subtract",
    "tasks": [{"id": "CALC-001", "description": "Add subtract", "acceptance_criteria": ["subtract(3, 1) == 2"]}],
}


def real_factory(model: ScriptedChatModel, monkeypatch):
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda name, timeout_s: model)
    return build_agent


def test_invoke_agent_deep_agent_retries_one_model_call_and_records_it(config, conn, tmp_path, monkeypatch):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    model = ScriptedChatModel(
        script=[
            tool_call("ls", {"path": "/"}, "c1"),
            HTTPError(503),
            tool_call("Plan", PLAN_ARGS, "c2"),
        ]
    )
    delays: list[float] = []
    ctx = AgentContext(
        config=config, conn=conn, layer="chat", workdir=tmp_path, factory=real_factory(model, monkeypatch), sleep=delays.append
    )
    plan = invoke_agent(get_spec("architect"), architect_packet(), ctx, node="architect")
    assert plan.keyword == "CALC"
    assert len(model.received) == 3
    # the retry resent call 2's conversation (with the ls result); a re-run would resend call 1's
    assert model.received[2] == model.received[1] > model.received[0]
    assert delays == [1.0]
    row = conn.execute("SELECT outcome, retries, model_calls FROM telemetry").fetchone()
    assert (row["outcome"], row["retries"], row["model_calls"]) == ("ok", 1, 2)


def test_invoke_agent_does_not_rerun_the_agent_after_the_middleware_gave_up(config, conn, tmp_path, monkeypatch):
    model = ScriptedChatModel(
        script=[tool_call("ls", {"path": "/"}, "c1"), HTTPError(429), HTTPError(429), HTTPError(429)]
    )
    delays: list[float] = []
    ctx = AgentContext(
        config=config, conn=conn, layer="chat", workdir=tmp_path, factory=real_factory(model, monkeypatch), sleep=delays.append
    )
    with pytest.raises(HTTPError):
        invoke_agent(get_spec("architect"), architect_packet(), ctx, node="architect")
    assert len(model.received) == 4  # 1 ok + 3 tries of the failing call; the agent ran once
    assert model.received[1] == model.received[2] == model.received[3] > model.received[0]
    assert delays == [1.0, 2.0]
    row = conn.execute("SELECT outcome, retries FROM telemetry").fetchone()
    assert (row["outcome"], row["retries"]) == ("error", 2)


def test_sub_agent_model_calls_are_retried_in_place(config, conn, tmp_path, monkeypatch):
    task_args = {"description": "list the files", "subagent_type": "general-purpose"}
    model = ScriptedChatModel(
        script=[
            tool_call("task", task_args, "c1"),  # main agent delegates to the general-purpose sub-agent
            HTTPError(503),  # the sub-agent's first model call fails ...
            usage(AIMessage(content="calc.py")),  # ... and its retry answers
            tool_call("Plan", PLAN_ARGS, "c2"),  # main agent finishes
        ]
    )
    delays: list[float] = []
    ctx = AgentContext(
        config=config, conn=conn, layer="chat", workdir=tmp_path, factory=real_factory(model, monkeypatch), sleep=delays.append
    )
    invoke_agent(get_spec("architect"), architect_packet(), ctx, node="architect")
    assert len(model.received) == 4  # the main agent's first call was not repeated
    row = conn.execute("SELECT retries, model_calls FROM telemetry").fetchone()
    assert (row["retries"], row["model_calls"]) == (1, 3)


def test_lean_agent_retries_its_model_call(config, conn, monkeypatch):
    from phil.contracts import CriticInput, Plan, Task

    model = ScriptedChatModel(
        script=[TimeoutError(), tool_call("PlanCritique", critique().model_dump(), "c1")]
    )
    ctx = AgentContext(config=config, conn=conn, layer="chat", factory=real_factory(model, monkeypatch), sleep=lambda _: None)
    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    packet = build_packet(
        "critic",
        CriticInput(goal=Goal(objective="Add subtract"), plan=Plan(keyword="CALC", description="x", tasks=[task])),
        budget_tokens=4000,
    )
    assert invoke_agent(get_spec("critic"), packet, ctx, node="critic").verdict == "ok"
    assert len(model.received) == 2
    assert conn.execute("SELECT retries FROM telemetry").fetchone()["retries"] == 1
