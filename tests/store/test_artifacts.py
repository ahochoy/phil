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


def test_write_log(tmp_path):
    store = ArtifactStore(tmp_path / "r-0001")
    path = store.write_log("verify-MAPS-001-1", "FAILED test_x\n")
    assert path == tmp_path / "r-0001" / "logs" / "verify-MAPS-001-1.log"
    assert path.read_text() == "FAILED test_x\n"
