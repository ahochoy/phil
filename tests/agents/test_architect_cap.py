"""The architect's model calls are capped, like the light agents' (live run: 46 calls, $3.17, one plan)."""

import dataclasses

from phil.agents.factory import BUDGET_GRACE_CALLS, build_agent
from phil.agents.registry import ARCHITECT_MAX_MODEL_CALLS, get_spec
from phil.agents.spec import PLAN_NOW
from tests.agents.test_light_harness import ToolRecordingModel
from tests.agents.test_model_retry import tool_call


def test_the_architect_spec_is_capped():
    spec = get_spec("architect")
    assert spec.harness == "deep"
    assert spec.max_model_calls == ARCHITECT_MAX_MODEL_CALLS == 20
    assert spec.cap_message == PLAN_NOW


def test_the_deep_harness_caps_and_hard_stops_the_architect(tmp_path, monkeypatch):
    spec = dataclasses.replace(get_spec("architect"), max_model_calls=3)
    invalid_plans = [tool_call("Plan", {}, f"c{i}") for i in range(50)]
    model = ToolRecordingModel(script=[tool_call("ls", {"path": "/"}, "l1"), *invalid_plans])
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    agent = build_agent(spec, "openrouter:x", tmp_path, [])
    result = agent.invoke({"messages": [{"role": "user", "content": "plan it"}]})
    assert result.get("structured_response") is None
    assert len(model.received) == 3 + BUDGET_GRACE_CALLS
    assert model.bound[2] == ["Plan"]  # from the cap on, only the plan tool is left
    assert PLAN_NOW in model.systems[2]
