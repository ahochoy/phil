from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from phil.contracts import Plan
from phil.run.engine import RunEngine
from phil.run.state import initial_state


@dataclass(frozen=True)
class RunOutcome:
    status: str
    escalation: dict | None = None


def thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _drive(engine: RunEngine, graph: Any, payload: Any) -> RunOutcome:
    config = thread_config(engine.deps.run_id)
    graph.invoke(payload, config)
    snapshot = graph.get_state(config)
    if snapshot.interrupts:
        return RunOutcome(status="escalated", escalation=snapshot.interrupts[0].value)
    return RunOutcome(status=snapshot.values.get("status", "completed"))


def start(engine: RunEngine, graph: Any, *, plan: Plan, base_sha: str, test_cmd: str) -> RunOutcome:
    return _drive(engine, graph, initial_state(engine.deps.run_id, plan, base_sha, test_cmd))


def resume(engine: RunEngine, graph: Any, decision: dict) -> RunOutcome:
    return _drive(engine, graph, Command(resume=decision))


def continue_run(engine: RunEngine, graph: Any) -> RunOutcome:
    return _drive(engine, graph, None)
