import os

import pytest

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.config import PhilConfig
from phil.contracts import CriticInput, Goal, Plan, PlanCritique, Task
from phil.packets import build_packet
from phil.store.db import connect

pytestmark = pytest.mark.live


def test_critic_returns_a_valid_critique(tmp_path):
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    task = Task(id="CALC-001", description="Add subtract(a, b) to calc.py", acceptance_criteria=["subtract(3, 1) == 2"])
    plan = Plan(keyword="CALC", description="Add a subtract function", tasks=[task], test_cmd="uv run pytest")
    packet = build_packet("critic", CriticInput(goal=Goal(objective="Add subtraction"), plan=plan), budget_tokens=4000)
    conn = connect(tmp_path / "phil.db")
    model = os.environ.get("PHIL_LIVE_MODEL", "openrouter:openai/gpt-6-luna")
    ctx = AgentContext(config=PhilConfig(models={"critic": model}), conn=conn, layer="chat")
    result = invoke_agent(get_spec("critic"), packet, ctx, node="critic")
    assert isinstance(result, PlanCritique)
    [row] = [dict(r) for r in conn.execute("SELECT * FROM telemetry WHERE outcome = 'ok'")]
    assert row["input_tokens"] > 0
    # 4c: the collector sees the real model call (not just the returned messages), and its cost
    # is either reported by the provider or estimated from the cached OpenRouter price list.
    assert row["model_calls"] >= 1
    assert row["cost_source"] in ("reported", "estimated")
    calls = [dict(r) for r in conn.execute("SELECT * FROM calls WHERE telemetry_id = ?", (row["id"],))]
    assert len(calls) >= 1


def test_architect_deep_agent_counts_every_model_call(tmp_path):
    # A deep agent's returned messages show only its own top-level turns; the collector must
    # also count sub-agent and summarisation model calls, so model_calls is at least the number
    # of top-level AI messages.
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    from langchain_core.messages import AIMessage

    from phil.agents.factory import build_agent
    from phil.contracts import ArchitectInput

    workdir = tmp_path / "repo"
    workdir.mkdir()
    (workdir / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    results: list[dict] = []

    def capturing_factory(spec, model, workdir, tools, *, timeout_s=180, provider=None):
        agent = build_agent(spec, model, workdir, tools, timeout_s=timeout_s, provider=provider)

        class Capturing:
            def invoke(self, payload, config=None):
                result = agent.invoke(payload, config=config)
                results.append(result)
                return result

        return Capturing()

    packet = build_packet(
        "architect",
        ArchitectInput(goal=Goal(objective="Add subtract(a, b) to calc.py"), repo_overview="calc.py: add(a, b)"),
        budget_tokens=4000,
    )
    conn = connect(tmp_path / "phil.db")
    model = os.environ.get("PHIL_LIVE_MODEL", "openrouter:openai/gpt-6-luna")
    ctx = AgentContext(
        config=PhilConfig(models={"architect": model}), conn=conn, layer="chat", workdir=workdir,
        factory=capturing_factory,
    )
    plan = invoke_agent(get_spec("architect"), packet, ctx, node="architect")
    assert isinstance(plan, Plan)
    [row] = [dict(r) for r in conn.execute("SELECT * FROM telemetry WHERE outcome = 'ok'")]
    top_level_ai = sum(isinstance(m, AIMessage) for m in results[-1]["messages"])
    assert top_level_ai >= 1
    assert row["model_calls"] >= top_level_ai
    assert row["cost_source"] in ("reported", "estimated")
