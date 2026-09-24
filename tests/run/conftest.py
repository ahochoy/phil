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
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, get_run
from tests.helpers import run_git

TEST_CMD = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"
RUN_ID = "r-0001"


@pytest.fixture
def calc_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "calc"
    repo.mkdir()
    repo = repo.resolve()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "init")
    return repo


def self_check() -> SelfCheck:
    return SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])


def task_result(phase: str, files=(), tests=()) -> TaskResult:
    return TaskResult(
        phase=phase, summary="done", files_changed=list(files), tests_added=list(tests), self_check=self_check()
    )


def tester_report(issues=()) -> TesterReport:
    return TesterReport(tests_added=[], issues=list(issues), self_check=self_check())


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
        from langgraph.types import Command

        return self.graph.invoke(Command(resume=decision), self.thread)

    def run_record(self):
        return get_run(self.deps.conn, self.deps.run_id)


@pytest.fixture
def make_harness(calc_repo: Path):
    def _make(scripts: dict, plan: Plan | None = None, config: PhilConfig | None = None, usage=(100, 20, 0.0)):
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
            config=config or PhilConfig(), conn=conn, repo_root=calc_repo, run_id=RUN_ID,
            worktree=paths.worktree_dir(RUN_ID), artifacts=ArtifactStore(paths.run_dir(RUN_ID)),
            factory=factory, sleep=lambda _: None,
        )
        engine = RunEngine(deps)
        return Harness(engine, engine.build(open_checkpointer(paths.db_path)), deps, factory, plan, base_sha)

    return _make
