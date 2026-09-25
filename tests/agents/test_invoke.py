import json
import shlex
import sys

import pytest

from phil.agents.fake import FakeAgent, FakeAgentFactory
from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.registry import get_spec
from phil.config import PhilConfig, ShellConfig
from phil.contracts import ImplementInput, PlanCritique, Task, TaskResult
from phil.packets import build_packet
from tests.helpers import TEST_MODEL, TEST_MODELS
from tests.agents.conftest import critique, self_check


def telemetry(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM telemetry ORDER BY id")]


def context(config, conn, artifacts, factory, **overrides):
    values = dict(config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=factory)
    return AgentContext(**(values | overrides))


def test_valid_output_is_returned_recorded_and_saved(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique()], usage=(120, 30, 0.002))
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert isinstance(result, PlanCritique)
    assert factory.built == [("critic", TEST_MODEL)]
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


def test_rejected_attempt_saves_raw_output_and_problems(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([{"verdict": "maybe"}, critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    rejected = json.loads((artifacts.run_dir / "outputs" / "critic-run-1.rejected.json").read_text())
    assert rejected["raw"] == {"verdict": "maybe"}
    assert rejected["problems"]


def test_second_attempt_saves_the_retry_payload(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([{"verdict": "maybe"}, critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    retry = json.loads((artifacts.run_dir / "packets" / "critic-run-2.retry.json").read_text())
    assert len(retry["messages"]) == 2
    assert "verdict" in retry["messages"][1]["content"]


def test_structured_output_parse_error_is_treated_as_invalid_and_retried(config, conn, artifacts, critic_packet):
    class StructuredOutputValidationError(Exception):
        pass

    factory = FakeAgentFactory([StructuredOutputValidationError("could not parse"), critique()])
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert isinstance(result, PlanCritique)
    assert [row["outcome"] for row in telemetry(conn)] == ["invalid", "ok"]


def test_contract_violation_carries_last_rejected_path(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([None, {"verdict": "maybe"}])
    with pytest.raises(ContractViolation) as excinfo:
        invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert excinfo.value.rejected_path is not None
    assert excinfo.value.rejected_path.endswith("critic-run-2.rejected.json")


def test_works_without_artifacts_or_run(config, conn, critic_packet):
    factory = FakeAgentFactory([critique()])
    ctx = context(config, conn, None, factory, layer="chat", run_id=None)
    assert isinstance(invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic"), PlanCritique)
    assert telemetry(conn)[0]["run_id"] is None


def test_shell_tool_given_only_to_shell_roles_with_workdir(config, conn, artifacts, critic_packet, tmp_path):
    factory = FakeAgentFactory([critique(), critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory, workdir=tmp_path), node="critic")
    assert factory.tools_seen == [[]]


def test_call_discriminator_keeps_artifacts_and_telemetry_distinct(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique(), critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic", call=1)
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic", call=2)
    assert (artifacts.run_dir / "packets" / "critic-run-1.json").exists()
    assert (artifacts.run_dir / "packets" / "critic-c2-run-1.json").exists()
    assert (artifacts.run_dir / "outputs" / "critic-run-1.json").exists()
    assert (artifacts.run_dir / "outputs" / "critic-c2-run-1.json").exists()
    rows = telemetry(conn)
    assert [row["call"] for row in rows] == [1, 2]
    assert [row["node"] for row in rows] == ["critic", "critic"]


def test_shell_role_without_workdir_raises(config, conn, artifacts):
    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    packet = build_packet("implementer", ImplementInput(task=task, phase="red", test_cmd="pytest"), budget_tokens=4000)
    factory = FakeAgentFactory([])
    ctx = context(config, conn, artifacts, factory, workdir=None)
    with pytest.raises(ValueError, match="implementer needs a workdir for its shell tool"):
        invoke_agent(get_spec("implementer"), packet, ctx, node="implement", task_id="CALC-001")


def test_packet_contract_type_mismatch_raises(config, conn, artifacts):
    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    wrong_packet = build_packet(
        "implementer", ImplementInput(task=task, phase="red", test_cmd="pytest"), budget_tokens=4000
    )
    factory = FakeAgentFactory([])
    with pytest.raises(ValueError, match="CriticInput") as excinfo:
        invoke_agent(get_spec("critic"), wrong_packet, context(config, conn, artifacts, factory), node="critic")
    assert "ImplementInput" in str(excinfo.value)


def test_shell_log_prefix_matches_artifact_base_name(conn, artifacts, tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    shell_config = ShellConfig(allow=[f"{shlex.quote(sys.executable)} *"])
    config = PhilConfig(shell=shell_config, models=TEST_MODELS)
    captured: dict = {}

    def factory(spec, model, workdir, tools):
        captured["tools"] = tools
        return FakeAgent([TaskResult(phase="red", summary="s", files_changed=[], tests_added=[], self_check=self_check())])

    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    packet = build_packet("implementer", ImplementInput(task=task, phase="red", test_cmd="pytest"), budget_tokens=4000)
    ctx = AgentContext(
        config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=factory, workdir=tmp_path
    )
    invoke_agent(get_spec("implementer"), packet, ctx, node="implement", task_id="CALC-001")
    [run_shell] = captured["tools"]
    run_shell(f"{shlex.quote(sys.executable)} hello.py")
    assert (artifacts.run_dir / "logs" / "implement-CALC-001-1-shell-1.log").exists()
