"""The architect's model calls are capped, like the light agents' (live run: 46 calls, $3.17, one plan)."""

import dataclasses

from phil.agents.factory import BUDGET_GRACE_CALLS, TOOL_BUDGET_USED, build_agent
from phil.agents.registry import ARCHITECT_MAX_MODEL_CALLS, get_spec
from phil.agents.spec import PLAN_NOW
from phil.contracts import Plan
from tests.chat.conftest import plan
from tests.agents.test_light_harness import ToolRecordingModel
from tests.agents.test_model_retry import tool_call


def test_the_architect_spec_is_capped():
    spec = get_spec("architect")
    # Light: no `task` sub-agent, whose model calls the cap can't see (live, one added 3 calls).
    assert spec.harness == "light"
    assert spec.max_model_calls == ARCHITECT_MAX_MODEL_CALLS == 10
    assert spec.cap_message == PLAN_NOW


def test_the_architect_prompt_states_its_call_budget():
    # Live, Sonnet read until the cap whatever it already knew: it has to be told the budget.
    from phil.agents.spec import load_prompt

    prompt = load_prompt(get_spec("architect"))
    assert f"about {ARCHITECT_MAX_MODEL_CALLS} model calls" in prompt
    assert prompt.index("## Exploration budget") < prompt.index("## Tasks")


class MessageRecordingModel(ToolRecordingModel):
    """Also keeps every call's messages (system message excluded)."""

    calls: list[list] = []

    def model_post_init(self, _context) -> None:
        super().model_post_init(_context)
        self.calls = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append([m for m in messages if m.type != "system"])
        return super()._generate(messages, stop, run_manager, **kwargs)


def _architect(tmp_path, monkeypatch, script):
    (tmp_path / "page.tsx").write_text("export default function Home() { return <main /> }\n")
    spec = dataclasses.replace(get_spec("architect"), max_model_calls=3)
    model = MessageRecordingModel(script=script)
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: model)
    agent = build_agent(spec, "openrouter:x", tmp_path, [])
    return model, agent.invoke({"messages": [{"role": "user", "content": "plan it"}]})


PLAN_ARGS = plan().model_dump(mode="json")


def test_a_capped_call_sees_earlier_tool_calls_only_as_text(tmp_path, monkeypatch):
    # Live, Claude through OpenRouter kept calling read_file and ls after the cap, though only Plan
    # was offered: earlier tool calls in the history invite more. At the cap they're plain text.
    script = [
        tool_call("ls", {"path": "/"}, "c1"),
        tool_call("read_file", {"file_path": "/page.tsx"}, "c2"),
        tool_call("Plan", PLAN_ARGS, "c3"),
    ]
    model, result = _architect(tmp_path, monkeypatch, script)
    assert result["structured_response"] == Plan.model_validate(PLAN_ARGS)
    assert [m.type for m in model.calls[1]] == ["human", "ai", "tool"]  # before the cap: unchanged
    [capped] = model.calls[2]
    assert capped.type == "human" and capped.text.startswith("plan it")
    assert "You called read_file" in capped.text and "export default function Home" in capped.text


def test_tools_called_past_the_cap_are_refused(tmp_path, monkeypatch):
    script = [
        tool_call("ls", {"path": "/"}, "c1"),
        tool_call("ls", {"path": "/"}, "c2"),
        tool_call("read_file", {"file_path": "/page.tsx"}, "c3"),  # the capped call ignores the cap
        tool_call("Plan", PLAN_ARGS, "c4"),
    ]
    model, result = _architect(tmp_path, monkeypatch, script)
    assert result["structured_response"] is not None
    [refused] = [m for m in result["messages"] if m.type == "tool" and m.tool_call_id == "c3"]
    assert refused.content == TOOL_BUDGET_USED
    assert "export default function Home" not in model.calls[3][0].text  # the file was never read


def test_repo_reading_prompts_say_where_paths_start():
    # Live, the architect spent 2 of its first 5 calls on "/repo/..." paths that don't exist.
    from phil.agents.spec import load_prompt

    for name in ("architect", "design", "answer", "implementer"):
        assert "there is no `/repo` or other prefix" in load_prompt(get_spec(name)), name


def test_the_architect_is_capped_and_hard_stopped(tmp_path, monkeypatch):
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
