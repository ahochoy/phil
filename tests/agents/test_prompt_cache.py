"""Prompt caching: Anthropic models through OpenRouter cache the repeated history (a capped
architect resends 15-25k tokens a call), and cache hits are recorded and shown."""

import uuid

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from phil.agents.collector import UsageCollector
from phil.agents.providers import BUILTIN_PROVIDERS, build_chat_model
from phil.store.db import connect
from phil.store.telemetry import TelemetryRow, record, usage_by_role
from phil.ui.show_view import _usage_table
from phil.ui.theme import make_console

ENVIRON = {"OPENROUTER_API_KEY": "dummy-openrouter"}


def _params(model_name: str) -> dict:
    model = build_chat_model(BUILTIN_PROVIDERS["openrouter"], model_name, 30, environ=ENVIRON)
    return model._default_params


def test_anthropic_models_through_openrouter_ask_for_automatic_caching():
    assert _params("anthropic/claude-sonnet-5")["cache_control"] == {"type": "ephemeral"}


def test_other_models_dont_get_the_cache_field():
    assert "cache_control" not in _params("google/gemini-3.8-flash")
    assert "cache_control" not in _params("z-ai/glm-5.1")


def _cached_generation(input_tokens: int, cached: int) -> ChatGeneration:
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens, "output_tokens": 5, "total_tokens": input_tokens + 5,
            "input_token_details": {"cache_read": cached},
        },
        response_metadata={"cost": 0.01},
    )
    return ChatGeneration(message=message)


def test_the_collector_records_cache_reads():
    collector = UsageCollector()
    collector.on_llm_end(LLMResult(generations=[[_cached_generation(20_000, 15_000)]]), run_id=uuid.uuid4())
    [call] = collector.calls
    assert call.cache_read_tokens == 15_000


def test_cache_reads_are_stored_and_shown_next_to_input_tokens(tmp_path):
    conn = connect(tmp_path / "t.db")
    base = dict(
        run_id="r-1", layer="run", node="implement", role="implementer", model="m", attempt=1, packet_tokens=0,
        output_tokens=10, latency_ms=0, cost_usd=0.1, outcome="ok",
    )
    record(conn, TelemetryRow(**base, input_tokens=100_000, cache_read_tokens=80_000))
    record(conn, TelemetryRow(**base, input_tokens=50_000, cache_read_tokens=30_000))
    [line] = usage_by_role(conn, "r-1")
    assert (line.input_tokens, line.cache_read_tokens) == (150_000, 110_000)
    console = make_console(record=True, width=160)
    console.print(_usage_table([line]))
    assert "150,000 (110,000 cached)" in console.export_text()


def test_rows_without_cache_reads_show_input_tokens_alone(tmp_path):
    conn = connect(tmp_path / "t.db")
    record(conn, TelemetryRow(
        run_id="r-1", layer="run", node="implement", role="implementer", model="m", attempt=1, packet_tokens=0,
        input_tokens=1_000, output_tokens=10, latency_ms=0, cost_usd=0.1, outcome="ok",
    ))
    console = make_console(record=True, width=160)
    console.print(_usage_table(usage_by_role(conn, "r-1")))
    assert "1,000" in console.export_text() and "cached" not in console.export_text()
