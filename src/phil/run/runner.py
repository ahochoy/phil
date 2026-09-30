from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from phil.contracts import Plan
from phil.run.engine import RunEngine
from phil.run.state import initial_state
from phil.store.runs import update_run


@dataclass(frozen=True)
class RunOutcome:
    status: str
    escalation: dict | None = None


def thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _drive(engine: RunEngine, graph: Any, payload: Any) -> RunOutcome:
    config = thread_config(engine.deps.run_id)
    graph.invoke(payload, config)
    return settle(engine, graph.get_state(config))


def settle(engine: RunEngine, snapshot: Any) -> RunOutcome:
    """Turn a graph snapshot into an outcome, recording a pending pause on the run row and events."""
    if snapshot.interrupts:
        escalation = snapshot.interrupts[0].value
        update_run(
            engine.deps.conn,
            engine.deps.run_id,
            state="escalated",
            needs_attention=escalation.get("error") or escalation["summary"],
        )
        if engine.deps.events is not None:
            engine.deps.events.append("escalation", escalation=escalation)
        return RunOutcome(status="escalated", escalation=escalation)
    return RunOutcome(status=snapshot.values.get("status", "completed"))


def start(
    engine: RunEngine, graph: Any, *, plan: Plan, base_sha: str, test_cmd: str, config_test_cmd: str | None = None
) -> RunOutcome:
    return _drive(engine, graph, initial_state(engine.deps.run_id, plan, base_sha, test_cmd, config_test_cmd))


def resume(engine: RunEngine, graph: Any, decision: dict, update: dict | None = None) -> RunOutcome:
    """Answer the pending pause. `update` is merged into the state as the paused node resumes: unlike
    `graph.update_state`, it keeps the pause pending until then, so a resume that fails can be retried."""
    return _drive(engine, graph, Command(resume=decision, update=update) if update else Command(resume=decision))


def continue_run(engine: RunEngine, graph: Any) -> RunOutcome:
    return _drive(engine, graph, None)
