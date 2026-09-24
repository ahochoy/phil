import sqlite3
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from phil.store.db import utcnow


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


@dataclass(frozen=True)
class UsageLine:
    layer: str
    role: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def record(conn: sqlite3.Connection, row: TelemetryRow) -> None:
    data = row.model_dump() | {"created_at": utcnow()}
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    conn.execute(f"INSERT INTO telemetry ({columns}) VALUES ({placeholders})", tuple(data.values()))


def usage_by_role(conn: sqlite3.Connection, run_id: str) -> list[UsageLine]:
    rows = conn.execute(
        "SELECT layer, role, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
        " SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost_usd"
        " FROM telemetry WHERE run_id = ? GROUP BY layer, role ORDER BY layer, role",
        (run_id,),
    ).fetchall()
    return [UsageLine(**dict(row)) for row in rows]


def run_totals(conn: sqlite3.Connection, run_id: str) -> tuple[int, float]:
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,"
        " COALESCE(SUM(cost_usd), 0.0) AS cost FROM telemetry WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["tokens"]), float(row["cost"])
