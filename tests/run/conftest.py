import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.config import PhilConfig
from phil.contracts import Plan, Review, SelfCheck, Task, TaskResult, TesterReport
from phil.run.checkpoint import open_checkpointer
from phil.run.engine import RunDeps, RunEngine
from phil.run.state import initial_state
from phil.store.activity import ActivityLog
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.events import EventLog
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, get_run
from tests.helpers import MODELS_TOML, TEST_MODELS, copy_repo, run_git

# A stand-in for `pytest -q`: these tests run it about seven times each (see tests/support/minitest.py).
TEST_CMD = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).parents[1] / 'support' / 'minitest.py'))}"
RUN_ID = "r-0001"


@pytest.fixture
def calc_repo(tmp_path: Path, calc_repo_template: Path) -> Path:
    # calc_repo_template is in tests/conftest.py, so every folder that imports this fixture sees it.
    return copy_repo(calc_repo_template, tmp_path / "calc")


def _with_test_models(config: PhilConfig) -> PhilConfig:
    return config.model_copy(update={"models": TEST_MODELS | config.models})


def self_check() -> SelfCheck:
    return SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])


def task_result(phase: str, files=(), tests=()) -> TaskResult:
    return TaskResult(
        phase=phase, summary="done", files_changed=list(files), tests_added=list(tests), self_check=self_check()
    )


def tester_report(issues=()) -> TesterReport:
    return TesterReport(tests_added=[], issues=list(issues), self_check=self_check())


tester_report.__test__ = False  # name starts with "test"; not a pytest test


def review(verdict: str = "approve", issues=()) -> Review:
    return Review(verdict=verdict, issues=list(issues), assumption_resolutions=[], self_check=self_check())


def write_red(turn: Turn) -> TaskResult:
    (turn.workdir / "tests" / "test_sub.py").write_text(
        "from calc import subtract\n\n\ndef test_subtract():\n    assert subtract(3, 1) == 2\n"
    )
    return task_result("red", ["tests/test_sub.py"], ["tests/test_sub.py"])


def write_green(turn: Turn) -> TaskResult:
    calc = turn.workdir / "calc.py"
    calc.write_text(calc.read_text() + "\n\ndef subtract(a, b):\n    return a - b\n")
    return task_result("green", ["calc.py"])


def bad_green(turn: Turn) -> TaskResult:
    calc = turn.workdir / "calc.py"
    calc.write_text(calc.read_text() + "\n\ndef subtract(a, b):\n    return a + b\n")
    return task_result("green", ["calc.py"])


def calc_plan(*extra: Task) -> Plan:
    first = Task(
        id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"], files_hint=["calc.py"]
    )
    return Plan(keyword="CALC", description="Add arithmetic", tasks=[first, *extra], test_cmd=TEST_CMD)


@dataclass
class Harness:
    engine: RunEngine
    graph: object
    deps: RunDeps
    factory: ScriptedAgentFactory
    plan: Plan
    base_sha: str

    @property
    def thread(self) -> dict:
        return {"configurable": {"thread_id": self.deps.run_id}}

    def start(self) -> dict:
        return self.graph.invoke(initial_state(self.deps.run_id, self.plan, self.base_sha, TEST_CMD), self.thread)

    def resume(self, decision: dict) -> dict:
        """Answer the pending pause as the chat does: with the pause's id (R16), unless the
        decision names one itself."""
        from langgraph.types import Command

        pending = self.graph.get_state(self.thread).interrupts
        pause_id = pending[0].value.get("id") if pending else None
        if pause_id is not None and "id" not in decision:
            decision = {**decision, "id": pause_id}
        return self.graph.invoke(Command(resume=decision), self.thread)

    def run_record(self):
        return get_run(self.deps.conn, self.deps.run_id)


@pytest.fixture
def make_harness(calc_repo: Path):
    def _make(
        scripts: dict,
        plan: Plan | None = None,
        config: PhilConfig | None = None,
        usage=(100, 20, 0.0),
        activity: ActivityLog | None = None,
    ):
        plan = plan or calc_plan()
        paths = ProjectPaths("calc-test")
        conn = connect(paths.db_path)
        base_sha = run_git(calc_repo, "rev-parse", "HEAD").strip()
        if get_run(conn, RUN_ID) is None:
            create_run(
                conn, run_id=RUN_ID, keyword=plan.keyword, base_sha=base_sha,
                worktree=paths.worktree_dir(RUN_ID), tasks_total=len(plan.tasks),
            )
        factory = ScriptedAgentFactory(scripts, usage=usage)
        deps = RunDeps(
            config=_with_test_models(config or PhilConfig()), conn=conn, repo_root=calc_repo, run_id=RUN_ID,
            worktree=paths.worktree_dir(RUN_ID), artifacts=ArtifactStore(paths.run_dir(RUN_ID)),
            factory=factory, sleep=lambda _: None,
            events=EventLog(paths.run_dir(RUN_ID) / "events.jsonl"), activity=activity, paths=paths,
        )
        engine = RunEngine(deps)
        return Harness(engine, engine.build(open_checkpointer(paths.db_path)), deps, factory, plan, base_sha)

    return _make
