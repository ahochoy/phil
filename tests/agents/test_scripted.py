import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.agents.registry import get_spec
from phil.agents.usage import extract_usage


def test_roles_have_independent_scripts(tmp_path):
    factory = ScriptedAgentFactory({"critic": ["c1"], "reviewer": ["r1", "r2"]})
    critic = factory(get_spec("critic"), "m", None, [])
    reviewer = factory(get_spec("reviewer"), "m", None, [])
    assert reviewer.invoke({"messages": []})["structured_response"] == "r1"
    assert critic.invoke({"messages": []})["structured_response"] == "c1"
    assert factory.remaining() == {"critic": 0, "reviewer": 1}
    assert [role for role, _ in factory.calls] == ["reviewer", "critic"]


def test_callable_items_get_workdir_tools_and_payload(tmp_path):
    def run_shell(command: str) -> str:
        return f"ran {command}"

    def script(turn: Turn) -> str:
        (turn.workdir / "made.txt").write_text("x")
        return turn.tools["run_shell"]("ls") + " / " + turn.payload["messages"][0]["content"]

    factory = ScriptedAgentFactory({"implementer": [script]}, usage=(7, 3, 0.1))
    agent = factory(get_spec("implementer"), "m", tmp_path, [run_shell])
    result = agent.invoke({"messages": [{"role": "user", "content": "hi"}]})
    assert result["structured_response"] == "ran ls / hi"
    assert (tmp_path / "made.txt").exists()
    usage = extract_usage(result["messages"])
    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd) == (7, 3, 0.1)


def test_exceptions_are_raised_and_empty_scripts_fail_clearly():
    factory = ScriptedAgentFactory({"critic": [RuntimeError("boom")]})
    agent = factory(get_spec("critic"), "m", None, [])
    with pytest.raises(RuntimeError, match="boom"):
        agent.invoke({"messages": []})
    with pytest.raises(AssertionError, match="no scripted output left for critic"):
        agent.invoke({"messages": []})
