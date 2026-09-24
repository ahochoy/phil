import pytest

from phil.agents.fake import FakeAgentFactory
from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.registry import get_spec
from phil.config import DEFAULT_MODEL
from phil.contracts import PlanCritique
from tests.agents.conftest import critique


def telemetry(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM telemetry ORDER BY id")]


def context(config, conn, artifacts, factory, **overrides):
    values = dict(config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=factory)
    return AgentContext(**(values | overrides))


def test_valid_output_is_returned_recorded_and_saved(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique()], usage=(120, 30, 0.002))
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert isinstance(result, PlanCritique)
    assert factory.built == [("critic", DEFAULT_MODEL)]
    [row] = telemetry(conn)
    assert (row["role"], row["outcome"], row["attempt"]) == ("critic", "ok", 1)
    assert (row["input_tokens"], row["output_tokens"], row["packet_tokens"]) == (120, 30, critic_packet.tokens)
    assert (artifacts.run_dir / "packets" / "critic-run-1.json").exists()
    assert (artifacts.run_dir / "outputs" / "critic-run-1.json").exists()


def test_packet_is_sent_as_user_message(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    [message] = factory.agent.calls[0]["messages"]
    assert message == {"role": "user", "content": critic_packet.render()}


def test_dict_output_is_validated(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique().model_dump()])
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert result == critique()


def test_invalid_then_valid_retries_with_problems(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([{"verdict": "maybe"}, critique()])
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert isinstance(result, PlanCritique)
    assert [row["outcome"] for row in telemetry(conn)] == ["invalid", "ok"]
    retry_messages = factory.agent.calls[1]["messages"]
    assert len(retry_messages) == 2
    assert "verdict" in retry_messages[1]["content"]


def test_two_invalid_attempts_raise(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([None, {"verdict": "maybe"}])
    with pytest.raises(ContractViolation) as excinfo:
        invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert excinfo.value.agent == "critic"
    assert excinfo.value.problems
    assert [row["outcome"] for row in telemetry(conn)] == ["invalid", "invalid"]


def test_works_without_artifacts_or_run(config, conn, critic_packet):
    factory = FakeAgentFactory([critique()])
    ctx = context(config, conn, None, factory, layer="chat", run_id=None)
    assert isinstance(invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic"), PlanCritique)
    assert telemetry(conn)[0]["run_id"] is None


def test_shell_tool_given_only_to_shell_roles_with_workdir(config, conn, artifacts, critic_packet, tmp_path):
    factory = FakeAgentFactory([critique(), critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory, workdir=tmp_path), node="critic")
    assert factory.tools_seen == [[]]
