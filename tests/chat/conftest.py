import pytest

from phil.agents.fake import ScriptedAgentFactory
from phil.agents.invoke import AgentContext
from phil.config import PhilConfig
from phil.contracts import Goal, Issue, Plan, PlanCritique, SelfCheck, Task
from phil.contracts.routing import RouteJudgement
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from tests.helpers import TEST_MODELS
from tests.run.conftest import calc_repo  # noqa: F401


# What the router says when a chat test doesn't script it: a confident feature, so the message takes
# the full path (intake, then planning) as it did before routing.
DEFAULT_ROUTE = RouteJudgement(task_class="feature", confidence=0.9, needs_detail=0.1)


class ChatFactory(ScriptedAgentFactory):
    """A ScriptedAgentFactory for chat tests: without a "route" script, every message is routed as
    `DEFAULT_ROUTE`, and `remaining()` leaves the unscripted router out."""

    def __init__(self, scripts: dict[str, list[object]], **kw) -> None:
        super().__init__(scripts, **kw)
        self.default_route = "route" not in self.scripts

    def __call__(self, spec, *args, **kw):
        if spec.name == "route" and self.default_route:
            self.scripts["route"] = [DEFAULT_ROUTE]
        return super().__call__(spec, *args, **kw)

    def remaining(self) -> dict[str, int]:
        counts = super().remaining()
        if self.default_route:
            counts.pop("route", None)
        return counts


def empty_check() -> SelfCheck:
    return SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])


def goal(objective="Add subtract to calc", **kw) -> Goal:
    return Goal(objective=objective, **kw)


def plan(keyword="CALC", n=1, test_cmd="uv run pytest -q") -> Plan:
    tasks = [
        Task(id=f"{keyword}-{i:03d}", description=f"Step {i}", acceptance_criteria=[f"criterion {i}"])
        for i in range(1, n + 1)
    ]
    return Plan(keyword=keyword, description="Do it", tasks=tasks, test_cmd=test_cmd)


def critique(verdict="ok", issues=(), notes=()) -> PlanCritique:
    return PlanCritique(verdict=verdict, issues=list(issues), notes=list(notes), self_check=empty_check())


def issue(note, task_id=None) -> Issue:
    return Issue(severity="major", note=note, task_id=task_id)


@pytest.fixture
def chat_ctx(tmp_path):
    def _make(factory, config=None):
        return AgentContext(
            config=config or PhilConfig(models=TEST_MODELS),
            conn=connect(tmp_path / "phil.db"),
            layer="chat",
            artifacts=ArtifactStore(tmp_path / "chat"),
            factory=factory,
            sleep=lambda _: None,
        )

    return _make
