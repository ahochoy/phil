import pytest

from phil.contracts import Ref
from phil.store.db import connect
from phil.store.parked import (
    close_all_open,
    close_parked,
    list_parked,
    open_count,
    park,
    parked_id,
    set_parked_status,
)
from phil.store.paths import ProjectPaths
from phil.store.telemetry import TelemetryRow, record, run_totals, usage_by_role


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


def test_usage_groups_by_layer_and_role(conn):
    record(conn, row())
    record(conn, row(attempt=2, outcome="invalid"))
    record(conn, row(node="review", role="reviewer", input_tokens=3000, output_tokens=500, cost_usd=0.05))
    record(conn, row(run_id=None, layer="chat", role="orchestrator"))
    lines = usage_by_role(conn, "r-0001")
    assert [(line.layer, line.role, line.calls) for line in lines] == [
        ("run", "implementer", 2),
        ("run", "reviewer", 1),
    ]
    implementer = lines[0]
    assert (implementer.input_tokens, implementer.output_tokens) == (2000, 400)


def test_run_totals(conn):
    record(conn, row())
    record(conn, row(cost_usd=0.02))
    tokens, cost = run_totals(conn, "r-0001")
    assert tokens == 2400
    assert cost == pytest.approx(0.03)


def test_run_totals_for_unknown_run_is_zero(conn):
    assert run_totals(conn, "r-ffff") == (0, 0.0)


def test_park_assigns_sequential_ids(conn):
    source = Ref(label="reviewer note", path="runs/r-0001/outputs/review-run-1.json")
    first = park(conn, raised_by="reviewer", note="N+1 query in listings", why_not_now="not in MAPS", source=source)
    second = park(conn, raised_by="user", note="dark mode", why_not_now="later", source=source)
    assert (first.id, second.id) == ("P-001", "P-002")
    assert first.status == "open"


def test_list_and_count_open_items(conn):
    source = Ref(label="x", path="y")
    park(conn, raised_by="user", note="a", why_not_now="b", source=source)
    item = park(conn, raised_by="user", note="c", why_not_now="d", source=source)
    set_parked_status(conn, item.id, "dropped")
    assert [p.note for p in list_parked(conn)] == ["a"]
    assert len(list_parked(conn, status=None)) == 2
    assert open_count(conn) == 1


def test_set_status_on_missing_item_raises(conn):
    with pytest.raises(KeyError):
        set_parked_status(conn, "P-999", "dropped")


def test_set_status_rejects_invalid_status(conn):
    source = Ref(label="x", path="y")
    item = park(conn, raised_by="user", note="a", why_not_now="b", source=source)
    with pytest.raises(ValueError):
        set_parked_status(conn, item.id, "bogus")
    assert list_parked(conn, status=None) == [item]


@pytest.mark.parametrize(("text", "expected"), [
    ("P-001", "P-001"), ("p-001", "P-001"), ("P-1", "P-001"), ("3", "P-003"), (" P-012 ", "P-012"),
    ("P-1234", "P-1234"), ("foo", None), ("P-", None), ("P-0", None), ("-1", None), ("P-1a", None),
])
def test_an_item_id_can_be_typed_loosely(text, expected):
    assert parked_id(text) == expected


def test_closing_items_closes_only_open_ones(conn):
    source = Ref(label="x", path="y")
    for note in ("a", "b", "c"):
        park(conn, raised_by="user", note=note, why_not_now="later", source=source)
    set_parked_status(conn, "P-002", "dropped")
    closed, not_open = close_parked(conn, ["P-001", "P-002", "P-009", "P-001"], "dropped")
    assert (closed, not_open) == (["P-001"], ["P-002", "P-009"])
    assert [p.id for p in list_parked(conn)] == ["P-003"]


def test_closing_every_open_item_leaves_the_rows_and_numbering(conn):
    source = Ref(label="x", path="y")
    for note in ("a", "b"):
        park(conn, raised_by="user", note=note, why_not_now="later", source=source)
    assert close_all_open(conn, "promoted") == ["P-001", "P-002"]
    assert open_count(conn) == 0 and close_all_open(conn, "dropped") == []
    assert {p.status for p in list_parked(conn, status=None)} == {"promoted"}
    assert park(conn, raised_by="user", note="c", why_not_now="later", source=source).id == "P-003"
