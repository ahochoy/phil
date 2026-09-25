import pytest

from phil.config import PhilConfig
from tests.helpers import TEST_MODELS
from phil.contracts import CriticInput, Goal, Plan, PlanCritique, SelfCheck, Task
from phil.packets import build_packet
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect


def self_check(**overrides) -> SelfCheck:
    values = dict(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])
    return SelfCheck(**(values | overrides))


def critique(**overrides) -> PlanCritique:
    values = dict(verdict="ok", issues=[], notes=["fine"], self_check=self_check())
    return PlanCritique(**(values | overrides))


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "phil.db")


@pytest.fixture
def artifacts(tmp_path):
    return ArtifactStore(tmp_path / "runs" / "r-0001")


@pytest.fixture
def critic_packet():
    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    plan = Plan(keyword="CALC", description="Add subtract", tasks=[task])
    contract = CriticInput(goal=Goal(objective="Add subtract"), plan=plan)
    return build_packet("critic", contract, budget_tokens=4000)


@pytest.fixture
def config():
    return PhilConfig(models=TEST_MODELS)
