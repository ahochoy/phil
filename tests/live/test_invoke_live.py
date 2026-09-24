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
    ctx = AgentContext(config=PhilConfig(), conn=conn, layer="chat")
    result = invoke_agent(get_spec("critic"), packet, ctx, node="critic")
    assert isinstance(result, PlanCritique)
    [row] = [dict(r) for r in conn.execute("SELECT * FROM telemetry WHERE outcome = 'ok'")]
    assert row["input_tokens"] > 0
