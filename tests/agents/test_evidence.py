from phil.agents.evidence import check_evidence
from phil.agents.fake import FakeAgentFactory
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import Claim, TaskResult
from phil.store.parked import list_parked
from tests.agents.conftest import critique, self_check


def result(**overrides) -> TaskResult:
    values = dict(phase="red", summary="s", files_changed=[], tests_added=[], self_check=self_check())
    return TaskResult(**(values | overrides))


def test_claims_must_match_commands_run(tmp_path):
    output = result(self_check=self_check(evidence=[Claim(statement="tests fail", command="uv run pytest -q")]))
    assert check_evidence(output, commands=["uv run pytest -q"], workdir=None) == []
    problems = check_evidence(output, commands=[], workdir=None)
    assert problems == ["claimed command was never run: uv run pytest -q"]


def test_claims_without_commands_are_accepted():
    output = result(self_check=self_check(evidence=[Claim(statement="read the code")]))
    assert check_evidence(output, commands=[], workdir=None) == []


def test_tests_added_must_exist(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    output = result(tests_added=["tests/test_a.py", "tests/test_missing.py"])
    assert check_evidence(output, commands=[], workdir=tmp_path) == [
        "tests_added file does not exist: tests/test_missing.py"
    ]


def test_evidence_failure_is_retried(config, conn, artifacts, critic_packet):
    bad = critique(self_check=self_check(evidence=[Claim(statement="ran it", command="pytest")]))
    factory = FakeAgentFactory([bad, critique()])
    ctx = AgentContext(config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=factory)
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    outcomes = [row["outcome"] for row in conn.execute("SELECT outcome FROM telemetry ORDER BY id")]
    assert outcomes == ["evidence_fail", "ok"]


def test_assumptions_and_out_of_scope_are_recorded(config, conn, artifacts, critic_packet):
    output = critique(self_check=self_check(assumptions=["lat/lng are floats"], out_of_scope=["N+1 query in listings"]))
    ctx = AgentContext(
        config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=FakeAgentFactory([output])
    )
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    [entry] = artifacts.read_assumptions()
    assert (entry["node"], entry["assumption"]) == ("critic", "lat/lng are floats")
    [item] = list_parked(conn)
    assert (item.raised_by, item.note, item.run_id) == ("critic", "N+1 query in listings", "r-0001")
    assert item.source.path.endswith("critic-run-1.json")


def test_shell_role_with_workdir_gets_run_shell(config, conn, artifacts, tmp_path):
    from phil.contracts import ImplementInput, Task, TaskResult
    from phil.packets import build_packet

    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    packet = build_packet("implementer", ImplementInput(task=task, phase="red", test_cmd="pytest"), budget_tokens=4000)
    output = TaskResult(phase="red", summary="s", files_changed=[], tests_added=[], self_check=self_check())
    factory = FakeAgentFactory([output])
    ctx = AgentContext(
        config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=factory, workdir=tmp_path
    )
    invoke_agent(get_spec("implementer"), packet, ctx, node="implement", task_id="CALC-001")
    assert factory.tools_seen == [["run_shell"]]
