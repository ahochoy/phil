import pytest

from phil.agents.invoke import AgentContext
from phil.config import PhilConfig
from phil.contracts import Goal, Issue, Plan, PlanCritique, SelfCheck, Task
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from tests.helpers import TEST_MODELS
from tests.run.conftest import calc_repo  # noqa: F401


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
