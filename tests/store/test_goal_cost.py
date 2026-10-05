from phil.store.db import connect
from phil.store.telemetry import TelemetryRow, chat_cost_since, last_telemetry_id, record


def _row(**kw) -> TelemetryRow:
    base = dict(
        run_id=None, layer="chat", node="architect", role="architect", model="m", attempt=1, packet_tokens=0,
        input_tokens=10, output_tokens=1, latency_ms=0, cost_usd=0.5, outcome="ok", chat_id="c-1",
    )
    return TelemetryRow(**(base | kw))


def test_chat_cost_since_counts_only_this_chats_later_chat_rows(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert last_telemetry_id(conn) == 0
    record(conn, _row(cost_usd=2.0))  # before the goal began
    mark = last_telemetry_id(conn)
    record(conn, _row(cost_usd=0.25))
    record(conn, _row(cost_usd=0.5, node="critic"))
    record(conn, _row(cost_usd=9.0, chat_id="c-2"))  # another chat
    record(conn, _row(cost_usd=9.0, layer="run", run_id="r-1"))  # a run's own spend has its own budget
    assert chat_cost_since(conn, "c-1", mark) == 0.75
    assert chat_cost_since(conn, "c-1", 0) == 2.75
