import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from phil.store.db import utcnow

CostSource = Literal["reported", "estimated", "unknown"]

_SOURCE_RANK: dict[CostSource, int] = {"reported": 0, "estimated": 1, "unknown": 2}
_RANK_SOURCE: dict[int, CostSource] = {rank: source for source, rank in _SOURCE_RANK.items()}


def weakest(sources: Sequence[CostSource]) -> CostSource:
    if not sources:
        return "reported"
    return _RANK_SOURCE[max(_SOURCE_RANK[source] for source in sources)]


def format_cost(cost: float, source: CostSource) -> str:
    if source == "unknown" and cost == 0.0:
        return "$?"
    formatted = f"${cost:.2f}"
    if source == "estimated":
        return f"~{formatted}"
    if source == "unknown":
        return f"{formatted}?"
    return formatted


class TelemetryRow(BaseModel):
    run_id: str | None
    layer: Literal["chat", "run"]
    node: str
    role: str
    model: str
    attempt: int
    packet_tokens: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    outcome: Literal["ok", "invalid", "evidence_fail", "error"]
    call: int = 1
    chat_id: str | None = None
    model_calls: int = 0
    tool_calls: dict[str, int] = {}
    retries: int = 0
    cost_source: CostSource = "reported"


@dataclass(frozen=True)
class CallRow:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_source: CostSource


@dataclass(frozen=True)
class Totals:
    tokens: int
    cost_usd: float
    cost_source: CostSource


@dataclass(frozen=True)
class UsageLine:
    layer: str
    role: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model_calls: int
    tool_calls: dict[str, int]
    retries: int
    cost_source: CostSource


def record(conn: sqlite3.Connection, row: TelemetryRow) -> int:
    data = row.model_dump() | {"created_at": utcnow()}
    data["tool_calls"] = json.dumps(data["tool_calls"])
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    cursor = conn.execute(f"INSERT INTO telemetry ({columns}) VALUES ({placeholders})", tuple(data.values()))
    return int(cursor.lastrowid)


def record_calls(conn: sqlite3.Connection, telemetry_id: int, calls: Sequence[CallRow]) -> None:
    now = utcnow()
    conn.executemany(
        "INSERT INTO calls (telemetry_id, model, input_tokens, output_tokens, cost_usd, cost_source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (telemetry_id, call.model, call.input_tokens, call.output_tokens, call.cost_usd, call.cost_source, now)
            for call in calls
        ],
    )


_COST_SOURCE_RANK_SQL = "CASE cost_source WHEN 'unknown' THEN 2 WHEN 'estimated' THEN 1 ELSE 0 END"


def usage_by_role(conn: sqlite3.Connection, run_id: str) -> list[UsageLine]:
    rows = conn.execute(
        "SELECT layer, role, input_tokens, output_tokens, cost_usd, model_calls, retries, tool_calls, cost_source"
        " FROM telemetry WHERE run_id = ? ORDER BY layer, role",
        (run_id,),
    ).fetchall()
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["layer"], row["role"])
        agg = grouped.setdefault(
            key,
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model_calls": 0,
             "retries": 0, "tool_calls": {}, "cost_sources": []},
        )
        agg["calls"] += 1
        agg["input_tokens"] += row["input_tokens"]
        agg["output_tokens"] += row["output_tokens"]
        agg["cost_usd"] += row["cost_usd"]
        agg["model_calls"] += row["model_calls"]
        agg["retries"] += row["retries"]
        agg["cost_sources"].append(row["cost_source"])
        for name, count in json.loads(row["tool_calls"]).items():
            agg["tool_calls"][name] = agg["tool_calls"].get(name, 0) + count
    return [
        UsageLine(
            layer=layer,
            role=role,
            calls=agg["calls"],
            input_tokens=agg["input_tokens"],
            output_tokens=agg["output_tokens"],
            cost_usd=agg["cost_usd"],
            model_calls=agg["model_calls"],
            tool_calls=agg["tool_calls"],
            retries=agg["retries"],
            cost_source=weakest(agg["cost_sources"]),
        )
        for (layer, role), agg in grouped.items()
    ]


def run_totals(conn: sqlite3.Connection, run_id: str) -> tuple[int, float]:
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,"
        " COALESCE(SUM(cost_usd), 0.0) AS cost FROM telemetry WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["tokens"]), float(row["cost"])


def _totals_for(conn: sqlite3.Connection, where: str, params: tuple) -> Totals:
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,"
        " COALESCE(SUM(cost_usd), 0.0) AS cost,"
        f" COALESCE(MAX({_COST_SOURCE_RANK_SQL}), 0) AS cost_source_rank"
        f" FROM telemetry WHERE {where}",
        params,
    ).fetchone()
    return Totals(int(row["tokens"]), round(float(row["cost"]), 6), _RANK_SOURCE[row["cost_source_rank"]])


def run_usage(conn: sqlite3.Connection, run_id: str) -> Totals:
    return _totals_for(conn, "run_id = ?", (run_id,))


def chat_usage(conn: sqlite3.Connection, chat_id: str) -> Totals:
    return _totals_for(
        conn,
        "(layer = 'chat' AND chat_id = ?) OR run_id IN (SELECT run_id FROM runs WHERE chat_id = ?)",
        (chat_id, chat_id),
    )
