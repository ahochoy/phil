import json
import shlex
import sys
import uuid
from pathlib import Path

import pytest

from phil.agents.fake import FakeAgent, FakeAgentFactory
from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.pricing import PriceBook
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

    def factory(spec, model, workdir, tools, *, timeout_s=180):
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


# --- plan 4c: usage accounting -------------------------------------------------------------

OPENROUTER_MODEL = "openrouter:openai/gpt-6-sol"
PRICES_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "openrouter-models.json").read_text())


def price_book(tmp_path):
    return PriceBook(tmp_path / "prices.json", fetch=lambda: PRICES_FIXTURE, clock=lambda: 1_000_000.0)


def no_fetch_price_book(tmp_path):
    def boom():
        raise AssertionError("the price book should not have been consulted")

    return PriceBook(tmp_path / "prices-unused.json", fetch=boom, clock=lambda: 1_000_000.0)


def _llm_result(input_tokens, output_tokens, cost=None, model_name=None):
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    metadata = {}
    if cost is not None:
        metadata["cost"] = cost
    if model_name is not None:
        metadata["model_name"] = model_name
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata=metadata,
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


class CallbackAgent:
    """Fires the collector's hooks the way a real (deep) agent would: nested model calls and
    tool starts, including the structured-output tool, then returns a structured response."""

    def __init__(self, steps, output, *, errors=()):
        self.steps = steps
        self.output = output
        self.errors = list(errors)
        self.configs: list = []

    def invoke(self, payload, config=None):
        self.configs.append(config)
        [collector] = config["callbacks"]
        parent = uuid.uuid4()
        for kind, *args in self.steps:
            if kind == "llm":
                collector.on_llm_end(_llm_result(*args), run_id=uuid.uuid4(), parent_run_id=parent)
            else:
                collector.on_tool_start({"name": args[0]}, "", run_id=uuid.uuid4(), parent_run_id=parent)
        if self.errors:
            raise self.errors.pop(0)
        return {"messages": [], "structured_response": self.output}


def accounting_context(config, conn, artifacts, agent, **overrides):
    return context(config, conn, artifacts, lambda spec, model, workdir, tools, *, timeout_s=180: agent, **overrides)


def calls_rows(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM calls ORDER BY id")]


def test_collector_counts_every_model_and_tool_call(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig(models=TEST_MODELS | {"critic": OPENROUTER_MODEL})
    agent = CallbackAgent(
        [
            ("llm", 100, 10, 0.01, "openai/gpt-6-sol"),
            ("tool", "read_file"),
            ("llm", 200, 20, 0.02, "openai/gpt-6-sol"),
            ("tool", "read_file"),
            ("tool", "task"),
            ("llm", 300, 30, 0.03, "openai/gpt-6-sol"),
            ("tool", "PlanCritique"),
        ],
        critique(),
    )
    ctx = accounting_context(config, conn, artifacts, agent, chat_id="c-0001", prices=no_fetch_price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert (row["input_tokens"], row["output_tokens"]) == (600, 60)
    assert row["cost_usd"] == pytest.approx(0.06)
    assert row["model_calls"] == 3
    assert json.loads(row["tool_calls"]) == {"read_file": 2, "task": 1}
    assert row["cost_source"] == "reported"
    assert row["chat_id"] == "c-0001"
    assert row["retries"] == 0
    calls = calls_rows(conn)
    assert [(c["telemetry_id"], c["input_tokens"], c["output_tokens"], c["cost_source"]) for c in calls] == [
        (row["id"], 100, 10, "reported"),
        (row["id"], 200, 20, "reported"),
        (row["id"], 300, 30, "reported"),
    ]
    assert [c["cost_usd"] for c in calls] == pytest.approx([0.01, 0.02, 0.03])


def test_missing_cost_is_estimated_from_the_price_book(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig(models=TEST_MODELS | {"critic": OPENROUTER_MODEL})
    agent = CallbackAgent(
        [("llm", 1000, 100, 0.01, "openai/gpt-6-sol"), ("llm", 1000, 100, None, "openai/gpt-6-sol")], critique()
    )
    ctx = accounting_context(config, conn, artifacts, agent, prices=price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["cost_source"] == "estimated"
    assert row["cost_usd"] == pytest.approx(0.013)
    assert [(c["cost_source"], c["model"]) for c in calls_rows(conn)] == [
        ("reported", "openai/gpt-6-sol"),
        ("estimated", "openai/gpt-6-sol"),
    ]
    assert calls_rows(conn)[1]["cost_usd"] == pytest.approx(0.003)


def test_call_model_from_another_family_is_priced_as_the_configured_model(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig(models=TEST_MODELS | {"critic": OPENROUTER_MODEL})
    agent = CallbackAgent([("llm", 1000, 100, None, "gpt-6-luna-2026-01-01")], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["cost_source"] == "estimated"
    assert row["cost_usd"] == pytest.approx(0.003)  # priced as openai/gpt-6-sol


def test_call_model_in_the_configured_family_is_priced_as_itself(conn, artifacts, critic_packet, tmp_path):
    config = PhilConfig(models=TEST_MODELS | {"critic": OPENROUTER_MODEL})
    agent = CallbackAgent([("llm", 1000, 100, None, "openai/gpt-6-luna")], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["cost_usd"] == pytest.approx(0.0000001 * 1000 + 0.0000005 * 100)


def test_call_model_from_a_different_vendor_is_priced_as_itself_when_the_book_knows_it(
    conn, artifacts, critic_packet, tmp_path
):
    # A deep agent's sub-agent (or an OpenRouter fallback route) can report a model from a
    # vendor other than the one configured for the role; when the price book knows that exact
    # model, it should be used, not the configured model's (unrelated) rate.
    config = PhilConfig(models=TEST_MODELS | {"critic": OPENROUTER_MODEL})
    agent = CallbackAgent([("llm", 1000, 100, None, "anthropic/claude-flash")], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["cost_source"] == "estimated"
    assert row["cost_usd"] == pytest.approx(0.000003 * 1000 + 0.000012 * 100)


def test_unpriced_model_cost_is_unknown(config, conn, artifacts, critic_packet, tmp_path):
    agent = CallbackAgent([("llm", 50, 5, None, None), ("llm", 50, 5, 0.01, None)], critique())
    ctx = accounting_context(config, conn, artifacts, agent, prices=price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["cost_source"] == "unknown"
    assert row["cost_usd"] == pytest.approx(0.01)
    first, second = calls_rows(conn)
    assert (first["cost_source"], first["cost_usd"], first["model"]) == ("unknown", 0.0, TEST_MODEL)
    assert second["cost_source"] == "reported"


def test_scripted_factory_falls_back_to_message_usage(config, conn, artifacts, critic_packet, tmp_path):
    factory = FakeAgentFactory([critique()], usage=(120, 30, 0.002))
    ctx = context(config, conn, artifacts, factory, chat_id="c-0002", prices=no_fetch_price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert (row["input_tokens"], row["output_tokens"], row["cost_usd"]) == (120, 30, 0.002)
    assert (row["model_calls"], row["cost_source"], json.loads(row["tool_calls"])) == (0, "reported", {})
    assert row["chat_id"] == "c-0002"
    assert calls_rows(conn) == []
    assert factory.agent.calls  # still invoked through the config-accepting fake


def test_transient_retries_are_counted(config, conn, artifacts, critic_packet, tmp_path):
    agent = CallbackAgent([("llm", 10, 1, 0.001, None)], critique(), errors=[TimeoutError("slow")])
    ctx = accounting_context(config, conn, artifacts, agent, sleep=lambda _: None, prices=price_book(tmp_path))
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["retries"] == 1
    assert row["model_calls"] == 2  # the failed attempt's model call still cost money
    assert len(calls_rows(conn)) == 2
    assert all(config_ is not None for config_ in agent.configs)


def test_error_rows_carry_retries_and_calls(config, conn, artifacts, critic_packet, tmp_path):
    agent = CallbackAgent(
        [("llm", 10, 1, 0.001, None), ("tool", "read_file")],
        critique(),
        errors=[TimeoutError("slow"), PermissionError("denied")],
    )
    ctx = accounting_context(
        config, conn, artifacts, agent, sleep=lambda _: None, chat_id="c-0003", prices=price_book(tmp_path)
    )
    with pytest.raises(PermissionError):
        invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [row] = telemetry(conn)
    assert row["outcome"] == "error"
    assert row["retries"] == 1
    assert row["model_calls"] == 2
    assert (row["input_tokens"], row["output_tokens"]) == (20, 2)
    assert row["cost_usd"] == pytest.approx(0.002)
    assert json.loads(row["tool_calls"]) == {"read_file": 2}
    assert row["chat_id"] == "c-0003"
    assert len(calls_rows(conn)) == 2
