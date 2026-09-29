import json
from pathlib import Path

import pytest

from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run
from phil.store.telemetry import (
    CallRow,
    TelemetryRow,
    Totals,
    budget_warning_line,
    chat_usage,
    format_cost,
    record,
    record_calls,
    run_usage,
    usage_by_role,
    weakest,
)


@pytest.fixture
def conn(phil_home):
    return connect(ProjectPaths("demo-12345678").db_path)


def row(**overrides) -> TelemetryRow:
    values = dict(
        run_id="r-0001",
        layer="run",
        node="implement",
        role="implementer",
        model="m",
        attempt=1,
        packet_tokens=900,
        input_tokens=1000,
        output_tokens=200,
        latency_ms=1500,
        cost_usd=0.01,
        outcome="ok",
    )
    return TelemetryRow(**(values | overrides))


def test_record_returns_the_id_and_stores_the_new_fields(conn):
    telemetry_id = record(
        conn, row(run_id="r-1", tool_calls={"read_file": 2}, model_calls=3, retries=1, cost_source="estimated", chat_id="c-1")
    )
    stored = conn.execute("SELECT * FROM telemetry WHERE id = ?", (telemetry_id,)).fetchone()
    assert json.loads(stored["tool_calls"]) == {"read_file": 2}
    assert (stored["model_calls"], stored["retries"], stored["cost_source"], stored["chat_id"]) == (3, 1, "estimated", "c-1")


def test_record_calls(conn):
    telemetry_id = record(conn, row(run_id="r-1"))
    record_calls(
        conn,
        telemetry_id,
        [CallRow("openrouter:x", 10, 5, 0.01, "reported"), CallRow("openrouter:x", 20, 5, 0.0, "unknown")],
    )
    assert conn.execute("SELECT COUNT(*) FROM calls WHERE telemetry_id = ?", (telemetry_id,)).fetchone()[0] == 2


def test_run_usage_takes_the_weakest_cost_source(conn):
    record(conn, row(run_id="r-1", input_tokens=100, output_tokens=10, cost_usd=0.10, cost_source="reported"))
    record(conn, row(run_id="r-1", input_tokens=50, output_tokens=5, cost_usd=0.05, cost_source="estimated"))
    assert run_usage(conn, "r-1") == Totals(165, 0.15, "estimated")
    assert run_usage(conn, "r-none") == Totals(0, 0.0, "reported")


def test_chat_usage_includes_the_chats_runs(conn):
    # Note: the brief used run_id="r-1"; create_run enforces phil.git.RUN_ID_PATTERN
    # (r-XXXX, 4 hex digits), so this uses "r-0001" like the rest of the test suite.
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="a", worktree=Path("/wt"), tasks_total=1, chat_id="c-1")
    record(conn, row(run_id=None, layer="chat", chat_id="c-1", input_tokens=10, output_tokens=0, cost_usd=0.01))
    record(conn, row(run_id="r-0001", input_tokens=20, output_tokens=0, cost_usd=0.02))
    record(conn, row(run_id=None, layer="chat", chat_id="c-2", input_tokens=99, output_tokens=0, cost_usd=0.99))
    assert chat_usage(conn, "c-1") == Totals(30, 0.03, "reported")


def test_usage_by_role_aggregates_tools_and_retries(conn):
    record(conn, row(run_id="r-1", role="implementer", tool_calls={"run_shell": 2}, model_calls=2, retries=1))
    record(conn, row(run_id="r-1", role="implementer", tool_calls={"run_shell": 1, "read_file": 1}, model_calls=1))
    [line] = usage_by_role(conn, "r-1")
    assert line.tool_calls == {"run_shell": 3, "read_file": 1} and line.model_calls == 3 and line.retries == 1


def test_weakest_and_format_cost():
    assert weakest(["reported", "estimated"]) == "estimated"
    assert weakest(["estimated", "unknown"]) == "unknown"
    assert weakest([]) == "reported"
    assert format_cost(0.4212, "reported") == "$0.42"
    assert format_cost(0.4212, "estimated") == "~$0.42"
    assert format_cost(0.0, "unknown") == "$?"
    assert format_cost(0.4212, "unknown") == "$0.42?"


def test_budget_warning_line_uses_the_cost_variant_when_cost_is_the_larger_fraction():
    line = budget_warning_line(
        "r-7f3a", tokens=0, cost_usd=1.61, max_tokens=400_000, max_cost_usd=2.0, cost_source="reported"
    )
    assert line == "r-7f3a has used 80% of its budget ($1.61 of $2.00)."


def test_budget_warning_line_uses_the_tokens_variant_when_tokens_is_the_larger_fraction():
    line = budget_warning_line(
        "r-7f3a", tokens=320_000, cost_usd=0.0, max_tokens=400_000, max_cost_usd=2.0, cost_source="reported"
    )
    assert line == "r-7f3a has used 80% of its budget (320,000 of 400,000 tokens)."


def test_budget_warning_line_shows_an_estimated_cost_with_its_marker():
    line = budget_warning_line(
        "r-7f3a", tokens=0, cost_usd=1.61, max_tokens=400_000, max_cost_usd=2.0, cost_source="estimated"
    )
    assert line == "r-7f3a has used 80% of its budget (~$1.61 of $2.00)."
