"""A model that answers in prose instead of returning the structured output: each attempt makes one
model call, the rejected record keeps the text as `raw`, and the second attempt carries the retry
message. Without this, LangChain re-prompts a lean agent with the same messages until its
recursion limit (about 25 model calls)."""

import json

import pytest
from langchain_core.messages import AIMessage

from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.registry import SPECS, get_spec
from phil.contracts import CriticInput, Goal, IntakeInput, Plan, ReviewInput, Task, TestReport
from phil.packets import build_packet
from phil.store.artifacts import ArtifactStore
from tests.agents.test_model_retry import ScriptedChatModel, architect_packet, real_factory


def _plan() -> Plan:
    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    return Plan(keyword="CALC", description="Add subtract", tasks=[task])


def _packet(name: str):
    goal = Goal(objective="Add subtract")
    report = TestReport(command="pytest", passed=True, failures=[], log_path="x.log")
    contracts = {
        "intake": lambda: IntakeInput(message="add subtract"),
        "critic": lambda: CriticInput(goal=goal, plan=_plan()),
        "reviewer": lambda: ReviewInput(plan=_plan(), diff="", final_report=report),
    }
    return build_packet(name, contracts[name](), budget_tokens=8000)


def prose(n: int) -> list[AIMessage]:
    return [AIMessage(content=f"Here is my answer in prose ({i}).") for i in range(1, n + 1)]


def test_every_lean_production_spec_ends_on_text():
    assert {name for name, spec in SPECS.items() if spec.harness == "lean"} == {"intake", "critic", "reviewer"}
    assert all(spec.end_on_text for spec in SPECS.values() if spec.harness == "lean")


@pytest.mark.parametrize("name", ["intake", "critic", "reviewer"])
def test_a_lean_agent_answering_in_prose_makes_one_call_per_attempt(name, config, conn, tmp_path, monkeypatch):
    model = ScriptedChatModel(script=prose(30))
    artifacts = ArtifactStore(tmp_path / "run")
    ctx = AgentContext(config=config, conn=conn, layer="chat", artifacts=artifacts, factory=real_factory(model, monkeypatch))
    with pytest.raises(ContractViolation) as excinfo:
        invoke_agent(get_spec(name), _packet(name), ctx, node=name)
    assert excinfo.value.problems == ["no structured output was returned"]
    assert len(model.received) == 2  # one model call per attempt, not ~25
    assert model.received[1] == model.received[0] + 1  # attempt 2 adds the retry message
    retry = json.loads((artifacts.run_dir / "packets" / f"{name}-run-2.retry.json").read_text())
    assert retry["messages"][-1]["content"].startswith("Your previous output was rejected")
    first = json.loads((artifacts.run_dir / "outputs" / f"{name}-run-1.rejected.json").read_text())
    second = json.loads((artifacts.run_dir / "outputs" / f"{name}-run-2.rejected.json").read_text())
    assert first["raw"] == "Here is my answer in prose (1)."
    assert second["raw"] == "Here is my answer in prose (2)."


def test_a_deep_agent_answering_in_prose_makes_one_call_per_attempt(config, conn, tmp_path, monkeypatch):
    model = ScriptedChatModel(script=prose(30))
    artifacts = ArtifactStore(tmp_path / "run")
    workdir = tmp_path / "repo"
    workdir.mkdir()
    ctx = AgentContext(
        config=config, conn=conn, layer="chat", artifacts=artifacts, workdir=workdir,
        factory=real_factory(model, monkeypatch),
    )
    with pytest.raises(ContractViolation):
        invoke_agent(get_spec("architect"), architect_packet(), ctx, node="architect")
    assert len(model.received) == 2
    rejected = json.loads((artifacts.run_dir / "outputs" / "architect-run-1.rejected.json").read_text())
    assert rejected["raw"] == "Here is my answer in prose (1)."
