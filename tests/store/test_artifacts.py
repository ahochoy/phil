import json

import pytest

from phil.contracts import Brief, Plan, Task
from phil.store.artifacts import ArtifactStore, artifact_name


def make_plan() -> Plan:
    task = Task(id="MAPS-001", description="d", acceptance_criteria=["c"])
    return Plan(keyword="MAPS", description="d", tasks=[task])


def test_artifact_name():
    assert artifact_name("implement", "MAPS-001", 2) == "implement-MAPS-001-2"
    assert artifact_name("review", None, 1) == "review-run-1"


def test_plan_round_trip(tmp_path):
    store = ArtifactStore(tmp_path / "r-0001")
    path = store.write_plan(make_plan())
    assert path == tmp_path / "r-0001" / "plan.json"
    assert store.read_plan() == make_plan()


def test_write_and_read_contract(tmp_path):
    store = ArtifactStore(tmp_path / "r-0001")
    path = store.write("outputs", artifact_name("present", None, 1), Brief(headline="done"))
    assert path == tmp_path / "r-0001" / "outputs" / "present-run-1.json"
    assert store.read(path, Brief).headline == "done"


def test_assumption_ledger_appends(tmp_path):
    store = ArtifactStore(tmp_path / "r-0001")
    store.append_assumptions(node="implement", task_id="MAPS-001", assumptions=["lat/lng are floats"])
    store.append_assumptions(node="implement", task_id="MAPS-002", assumptions=["a", "b"])
    entries = store.read_assumptions()
    assert len(entries) == 3
    assert entries[0] == {
        "node": "implement",
        "task_id": "MAPS-001",
        "assumption": "lat/lng are floats",
        "status": "open",
    }


def test_read_assumptions_when_none(tmp_path):
    assert ArtifactStore(tmp_path / "r-0001").read_assumptions() == []


def test_write_json_writes_arbitrary_data(tmp_path):
    store = ArtifactStore(tmp_path / "r-0001")
    path = store.write_json("outputs", "critic-run-1.rejected", {"raw": {"verdict": "maybe"}, "problems": ["x"]})
    assert path == tmp_path / "r-0001" / "outputs" / "critic-run-1.rejected.json"
    assert json.loads(path.read_text()) == {"raw": {"verdict": "maybe"}, "problems": ["x"]}


def test_write_json_falls_back_to_repr_for_unserializable_values(tmp_path):
    store = ArtifactStore(tmp_path / "r-0001")

    class Weird:
        def __repr__(self) -> str:
            return "Weird()"

    path = store.write_json("outputs", "weird", {"raw": Weird()})
    assert json.loads(path.read_text()) == {"raw": "Weird()"}


def test_write_log(tmp_path):
    store = ArtifactStore(tmp_path / "r-0001")
    path = store.write_log("verify-MAPS-001-1", "FAILED test_x\n")
    assert path == tmp_path / "r-0001" / "logs" / "verify-MAPS-001-1.log"
    assert path.read_text() == "FAILED test_x\n"


@pytest.mark.parametrize(
    "relative",
    [
        "/etc/passwd",
        "../outside.json",
        "outputs/../../outside.json",
        "outputs/../../../outside.json",
    ],
)
def test_file_rejects_escaping_paths(tmp_path, relative):
    store = ArtifactStore(tmp_path / "r-0001")
    with pytest.raises(ValueError):
        store._file(relative)


@pytest.mark.parametrize("bad", ["../implement", "a/b", "a\\b", "..", "sub/../../x"])
def test_artifact_name_rejects_path_like_components(bad):
    with pytest.raises(ValueError):
        artifact_name(bad, "MAPS-001", 1)
    with pytest.raises(ValueError):
        artifact_name("implement", bad, 1)


def test_concurrent_append_assumptions_never_interleave(tmp_path, monkeypatch):
    import threading
    import time

    import phil.store.artifacts as artifacts

    real_dumps = json.dumps

    def slow_dumps(*a, **k):  # hand the GIL to another appender between one call's lines
        time.sleep(0.002)
        return real_dumps(*a, **k)

    real_open = artifacts.Path.open

    def line_buffered(self, mode="r", *a, **k):  # each line reaches the file on its own write
        return real_open(self, mode, 1, *a[1:], **k) if mode == "a" else real_open(self, mode, *a, **k)

    monkeypatch.setattr(artifacts.json, "dumps", slow_dumps)
    monkeypatch.setattr(artifacts.Path, "open", line_buffered)
    store = ArtifactStore(tmp_path)
    big = "x" * 200
    barrier = threading.Barrier(6)

    def append(n: int) -> None:
        barrier.wait()
        for i in range(5):
            store.append_assumptions(node=f"n{n}", task_id=None, assumptions=[f"{n}-{i}-a{big}", f"{n}-{i}-b{big}"])

    threads = [threading.Thread(target=append, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    entries = store.read_assumptions()  # every line parses
    assert len(entries) == 6 * 5 * 2
    labels = [e["assumption"].split("x", 1)[0] for e in entries]
    for a, b in zip(labels[::2], labels[1::2]):  # each call's lines stay together
        assert a.endswith("a") and b == a[:-1] + "b", labels
