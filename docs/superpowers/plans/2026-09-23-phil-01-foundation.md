# Phil Plan 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phil's LLM-free foundation: package layout, contracts, config, repo resolution, SQLite store, artifacts, git worktrees, a policy-checked shell runner, the themed console, and CLI commands (`--version`, `runs`, `parked`, `schema`).

**Architecture:** An installable `src/phil` package. Pure-Python modules with one responsibility each; no LangChain/LangGraph imports anywhere in this plan. Everything later plans need (contracts, store, workspace) is built and tested here first.

**Tech Stack:** Python 3.14, uv, hatchling, pydantic 2, typer, rich, sqlite3 (stdlib), tomllib (stdlib), git CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-phil-v1-design.md`

**Plan series:** 1 Foundation (this plan) → 2 Agent core (`AgentSpec`, `invoke_agent`, packets, `FakeAgent`, prompts) → 3 Run graph (nodes, TDD gates, worker, attach/resume/stop) → 4 Chat and interface (`ingest`/`present`, orchestrator, architect/critic, live smoke test).

## Global Constraints

- Python `>=3.14`; dependencies managed with `uv` (`uv add`, `uv sync`, `uv run`).
- Source lives in `src/phil/`; tests in `tests/`. Run tests with `uv run pytest`.
- Every contract inherits `Contract` (top level, has `schema_version`) or `Part` (nested, no version). Both forbid extra fields.
- `phil.cli.main` must not import `langchain`, `langgraph`, `deepagents`, or `langchain_openrouter`, directly or indirectly (spec §2: lazy imports).
- All terminal output goes through the console from `phil.ui.theme.make_console()` using `phil.*` style names. No hard-coded colors outside `src/phil/ui/`.
- Tests must never touch the real `~/.phil`: the autouse `phil_home` fixture sets `PHIL_HOME`.
- Phil state lives under `$PHIL_HOME/projects/<repo-slug>/` (default `PHIL_HOME=~/.phil`).
- Run branches are named `phil/<run-id>`; run ids match `^r-[0-9a-f]{4}$`.
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` after a blank line.

## File Structure

```
pyproject.toml                    modify: build system, scripts, deps, pytest config
README.md                         modify: what Phil is + dev commands
prototype/                        move: old graph.py, entrypoint.py, __init__.py, nodes/, prompts/
src/phil/__init__.py              __version__
src/phil/cli/__init__.py
src/phil/cli/main.py              typer app: --version, --repo, runs, parked, schema
src/phil/config.py                PhilConfig + load_config()
src/phil/repo.py                  RepoInfo + resolve_repo()
src/phil/contracts/__init__.py    re-exports + ALL_CONTRACTS
src/phil/contracts/base.py        Contract, Part
src/phil/contracts/common.py      Claim, SelfCheck, Issue
src/phil/contracts/planning.py    Task, Plan, PlanCritique
src/phil/contracts/results.py     TaskResult, TestReport, TesterReport, Review
src/phil/contracts/interface.py   Ref, Decision, Goal, Brief, ParkedItem, RunStatus
src/phil/contracts/schema.py      export_schemas()
src/phil/store/__init__.py
src/phil/store/paths.py           phil_home(), ProjectPaths
src/phil/store/db.py              connect(), utcnow(), SQL schema
src/phil/store/runs.py            RunRecord, new_run_id(), create/get/list/update_run
src/phil/store/telemetry.py       TelemetryRow, record(), usage_by_role(), run_totals()
src/phil/store/parked.py          park(), list_parked(), set_parked_status(), open_count()
src/phil/store/artifacts.py       ArtifactStore, artifact_name()
src/phil/workspace/__init__.py
src/phil/workspace/worktree.py    Worktree, WorktreeManager
src/phil/workspace/shell.py       ShellPolicy, ShellResult, run_command(), truncate_output()
src/phil/ui/__init__.py
src/phil/ui/theme.py              PHIL_THEME, make_console()
tests/__init__.py
tests/helpers.py                  run_git()
tests/conftest.py                 phil_home (autouse), git_repo fixtures
tests/test_cli.py
tests/test_config.py
tests/test_contracts.py
tests/test_repo.py
tests/store/__init__.py
tests/store/test_runs.py
tests/store/test_telemetry_parked.py
tests/store/test_artifacts.py
tests/workspace/__init__.py
tests/workspace/test_worktree.py
tests/workspace/test_shell.py
tests/test_ui.py
```

---

### Task 1: Package scaffold and `phil --version`

**Files:**
- Move: `graph.py`, `entrypoint.py`, `__init__.py`, `nodes/`, `prompts/` → `prototype/`
- Modify: `pyproject.toml`, `README.md`
- Create: `src/phil/__init__.py`, `src/phil/cli/__init__.py`, `src/phil/cli/main.py`, `tests/__init__.py`, `tests/helpers.py`, `tests/conftest.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `phil.__version__: str`; `phil.cli.main.app: typer.Typer`; test fixtures `phil_home` (autouse, returns `Path`) and `git_repo` (returns resolved `Path` of a repo on branch `main` with one commit containing `app.py`); helper `tests.helpers.run_git(repo: Path, *args: str) -> str`.

- [ ] **Step 1: Archive the prototype**

```bash
mkdir -p prototype
mv graph.py entrypoint.py __init__.py nodes prompts prototype/
rm -rf __pycache__
```

- [ ] **Step 2: Configure the package**

Edit `pyproject.toml` so the `[project]` table keeps its existing dependencies, and add the sections below. Change `description` too.

```toml
[project]
name = "phil"
version = "0.1.0"
description = "Contract-driven CLI coding agent"
readme = "README.md"
requires-python = ">=3.14"
# dependencies: keep existing list; uv add (next step) appends to it

[project.scripts]
phil = "phil.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/phil"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: calls real LLM APIs (opt-in with -m live)"]
addopts = "-m 'not live'"
```

Then add dependencies:

```bash
uv add typer pydantic
uv add --dev pytest
uv sync
```

Expected: `uv sync` builds and installs `phil` in editable mode without errors.

- [ ] **Step 3: Write test helpers and fixtures**

`tests/__init__.py`: empty file.

`tests/helpers.py`:

```python
import subprocess
from pathlib import Path


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
```

`tests/conftest.py`:

```python
from pathlib import Path

import pytest

from tests.helpers import run_git


@pytest.fixture(autouse=True)
def phil_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "phil_home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    return home


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    repo = repo.resolve()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "init")
    return repo
```

- [ ] **Step 4: Write the failing test**

`tests/test_cli.py`:

```python
from typer.testing import CliRunner

from phil import __version__
from phil.cli.main import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
```

- [ ] **Step 5: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil'` (or `phil.cli`).

- [ ] **Step 6: Implement**

`src/phil/__init__.py`:

```python
__version__ = "0.1.0"
```

`src/phil/cli/__init__.py`: empty file.

`src/phil/cli/main.py`:

```python
import typer

from phil import __version__

app = typer.Typer(add_completion=False, help="Phil: a contract-driven coding agent.")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Show version and exit."
    ),
) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo("Chat mode is not implemented yet.")
```

- [ ] **Step 7: Run tests and the installed command**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

Run: `uv run phil --version`
Expected: prints `0.1.0`

- [ ] **Step 8: Update README**

Replace `README.md` with:

```markdown
# Phil

Phil is a CLI coding agent built around explicit contracts between agents, managed context, and code-enforced gates. Run it inside a git repository; it plans a goal with you, then implements it in an isolated git worktree and leaves a tested, reviewed branch.

Design: `docs/superpowers/specs/2026-09-23-phil-v1-design.md`

## Development

    uv sync
    uv run pytest
    uv run phil --version

The original LangGraph prototype is kept in `prototype/` for reference and is not part of the package.
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock README.md .python-version prototype src tests
git commit -m "$(printf 'Scaffold phil package and archive prototype\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 2: Contracts and JSON Schema export

**Files:**
- Create: `src/phil/contracts/__init__.py`, `base.py`, `common.py`, `planning.py`, `results.py`, `interface.py`, `schema.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all importable from `phil.contracts`): `Contract`, `Part`, `Claim`, `SelfCheck`, `Issue`, `Task`, `Plan`, `PlanCritique`, `TaskResult`, `TestReport`, `TesterReport`, `Review`, `Ref`, `Decision`, `Goal`, `Brief`, `ParkedItem`, `RunStatus`, `ALL_CONTRACTS: list[type[Contract]]`. From `phil.contracts.schema`: `export_schemas(out_dir: Path) -> list[Path]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_contracts.py`:

```python
import json

import pytest
from pydantic import ValidationError

from phil.contracts import (
    ALL_CONTRACTS,
    Brief,
    Plan,
    SelfCheck,
    Task,
)
from phil.contracts.schema import export_schemas


def make_task(task_id: str = "MAPS-001") -> Task:
    return Task(
        id=task_id,
        description="Add lat/lng to Listing",
        acceptance_criteria=["Listing has lat and lng floats"],
        files_hint=["src/listing.py"],
    )


def test_task_defaults_to_todo():
    assert make_task().status == "TODO"


def test_task_id_must_match_keyword_pattern():
    with pytest.raises(ValidationError):
        make_task("maps-1")


def test_task_requires_acceptance_criteria():
    with pytest.raises(ValidationError):
        Task(id="MAPS-001", description="x", acceptance_criteria=[])


def test_plan_rejects_task_ids_with_other_keyword():
    with pytest.raises(ValidationError, match="keyword"):
        Plan(keyword="MAPS", description="d", tasks=[make_task("AUTH-001")])


def test_plan_rejects_duplicate_task_ids():
    with pytest.raises(ValidationError, match="duplicate"):
        Plan(keyword="MAPS", description="d", tasks=[make_task(), make_task()])


def test_plan_round_trips_through_json():
    plan = Plan(keyword="MAPS", description="d", tasks=[make_task()], story_ref="E4/S2")
    assert Plan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.schema_version == 1


def test_contracts_forbid_extra_fields():
    with pytest.raises(ValidationError):
        Plan(keyword="MAPS", description="d", tasks=[make_task()], surprise=True)


def test_self_check_fields_are_required():
    with pytest.raises(ValidationError):
        SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[])


def test_brief_headline_is_bounded():
    with pytest.raises(ValidationError):
        Brief(headline="x" * 121)


def test_brief_allows_at_most_five_points():
    with pytest.raises(ValidationError):
        Brief(headline="ok", points=["a", "b", "c", "d", "e", "f"])


def test_brief_points_are_bounded():
    with pytest.raises(ValidationError):
        Brief(headline="ok", points=["y" * 201])


def test_export_schemas_writes_one_file_per_contract(tmp_path):
    paths = export_schemas(tmp_path / "schemas")
    assert len(paths) == len(ALL_CONTRACTS)
    for path in paths:
        schema = json.loads(path.read_text())
        assert "properties" in schema
    assert (tmp_path / "schemas" / "Plan.schema.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.contracts'`

- [ ] **Step 3: Implement**

`src/phil/contracts/base.py`:

```python
from pydantic import BaseModel, ConfigDict


class Part(BaseModel):
    """Nested contract component. Rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class Contract(Part):
    """Top-level contract passed between agents or across the human boundary."""

    schema_version: int = 1
```

`src/phil/contracts/common.py`:

```python
from typing import Literal

from phil.contracts.base import Part


class Claim(Part):
    statement: str
    command: str | None = None
    observed_output: str | None = None


class SelfCheck(Part):
    assumptions: list[str]
    evidence: list[Claim]
    risks: list[str]
    unverified: list[str]
    out_of_scope: list[str]


class Issue(Part):
    task_id: str | None = None
    file: str | None = None
    line: int | None = None
    severity: Literal["blocker", "major", "minor"]
    note: str
```

`src/phil/contracts/planning.py`:

```python
from typing import Literal

from pydantic import Field, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.common import Issue, SelfCheck

TASK_ID_PATTERN = r"^[A-Z]{3,6}-\d{3}$"
KEYWORD_PATTERN = r"^[A-Z]{3,6}$"


class Task(Part):
    id: str = Field(pattern=TASK_ID_PATTERN)
    description: str
    acceptance_criteria: list[str] = Field(min_length=1)
    files_hint: list[str] = []
    status: Literal["TODO", "DONE", "SKIPPED", "FAILED"] = "TODO"


class Plan(Contract):
    keyword: str = Field(pattern=KEYWORD_PATTERN)
    description: str
    tasks: list[Task] = Field(min_length=1)
    test_cmd: str | None = None
    story_ref: str | None = None
    critic_notes: list[str] = []

    @model_validator(mode="after")
    def _check_task_ids(self) -> "Plan":
        for task in self.tasks:
            if not task.id.startswith(f"{self.keyword}-"):
                raise ValueError(f"task {task.id} does not use keyword {self.keyword}")
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task ids")
        return self


class PlanCritique(Contract):
    verdict: Literal["ok", "revise"]
    issues: list[Issue]
    notes: list[str]
    self_check: SelfCheck
```

`src/phil/contracts/results.py`:

```python
from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract
from phil.contracts.common import Issue, SelfCheck


class TaskResult(Contract):
    phase: Literal["red", "green"]
    summary: str = Field(max_length=600)
    files_changed: list[str]
    tests_added: list[str]
    self_check: SelfCheck


class TestReport(Contract):
    __test__ = False  # not a pytest test class

    command: str
    passed: bool
    failures: list[str]
    log_path: str
    new_failures_vs_baseline: list[str] = []


class TesterReport(Contract):
    tests_added: list[str]
    issues: list[Issue]
    self_check: SelfCheck


class Review(Contract):
    verdict: Literal["approve", "changes"]
    issues: list[Issue]
    assumption_resolutions: list[str]
    self_check: SelfCheck
```

`src/phil/contracts/interface.py`:

```python
from typing import Annotated, Literal

from pydantic import Field

from phil.contracts.base import Contract, Part


class Ref(Part):
    label: str
    path: str


class Decision(Part):
    question: str
    options: list[str]


class Goal(Contract):
    objective: str
    constraints: list[str] = []
    non_goals: list[str] = []
    open_questions: list[str] = []
    story_ref: str | None = None


class Brief(Contract):
    headline: str = Field(max_length=120)
    status: str | None = None
    needs_you: list[Decision] = []
    points: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=5)
    details: list[Ref] = []
    parked: int = Field(default=0, ge=0)


class ParkedItem(Contract):
    id: str
    raised_by: str
    note: str
    why_not_now: str
    source: Ref
    status: Literal["open", "promoted", "dropped"] = "open"
    run_id: str | None = None


class RunStatus(Contract):
    run_id: str
    state: str
    tasks_done: int
    tasks_total: int
    current_node: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    needs_attention: str | None = None
```

`src/phil/contracts/__init__.py`:

```python
from phil.contracts.base import Contract, Part
from phil.contracts.common import Claim, Issue, SelfCheck
from phil.contracts.interface import Brief, Decision, Goal, ParkedItem, Ref, RunStatus
from phil.contracts.planning import Plan, PlanCritique, Task
from phil.contracts.results import Review, TaskResult, TesterReport, TestReport

ALL_CONTRACTS: list[type[Contract]] = [
    Plan,
    PlanCritique,
    TaskResult,
    TestReport,
    TesterReport,
    Review,
    Goal,
    Brief,
    ParkedItem,
    RunStatus,
]

__all__ = [
    "ALL_CONTRACTS",
    "Brief",
    "Claim",
    "Contract",
    "Decision",
    "Goal",
    "Issue",
    "ParkedItem",
    "Part",
    "Plan",
    "PlanCritique",
    "Ref",
    "Review",
    "RunStatus",
    "SelfCheck",
    "Task",
    "TaskResult",
    "TesterReport",
    "TestReport",
]
```

`src/phil/contracts/schema.py`:

```python
import json
from pathlib import Path

from phil.contracts import ALL_CONTRACTS


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for model in ALL_CONTRACTS:
        path = out_dir / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        paths.append(path)
    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contracts.py -v`
Expected: all 12 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/contracts tests/test_contracts.py
git commit -m "$(printf 'Add inter-agent and interface contracts with schema export\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 3: Configuration (`phil.toml`)

**Files:**
- Create: `src/phil/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `phil.config.ROLES: tuple[str, ...]`, `DEFAULT_MODEL: str`, `RoleBudget(max_input_tokens: int)`, `RunConfig(tester_mode, max_attempts_per_phase, max_review_rounds, max_tokens, max_cost_usd)`, `ShellConfig(allow: list[str], timeout_s: int, max_output_lines: int)`, `ProjectConfig(test_cmd: str | None, test_globs: list[str])`, `PhilConfig` with methods `model_for(role: str) -> str` and `budget_for(role: str) -> RoleBudget`, and `load_config(repo_root: Path) -> PhilConfig`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from phil.config import DEFAULT_MODEL, ROLES, load_config


def test_missing_file_gives_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.run.tester_mode == "run"
    assert config.run.max_attempts_per_phase == 3
    assert all(config.model_for(role) == DEFAULT_MODEL for role in ROLES)


def test_partial_models_override_keeps_other_defaults(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nimplementer = "openrouter:cheap/model"\n')
    config = load_config(tmp_path)
    assert config.model_for("implementer") == "openrouter:cheap/model"
    assert config.model_for("reviewer") == DEFAULT_MODEL


def test_sections_are_loaded(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[run]\ntester_mode = "task+run"\n'
        "[budget.implementer]\nmax_input_tokens = 8000\n"
        '[project]\ntest_cmd = "uv run pytest -q"\n'
        '[shell]\nallow = ["pytest*"]\n'
    )
    config = load_config(tmp_path)
    assert config.run.tester_mode == "task+run"
    assert config.budget_for("implementer").max_input_tokens == 8000
    assert config.budget_for("reviewer").max_input_tokens == 12000
    assert config.project.test_cmd == "uv run pytest -q"
    assert config.shell.allow == ["pytest*"]


def test_invalid_tester_mode_is_rejected(tmp_path):
    (tmp_path / "phil.toml").write_text('[run]\ntester_mode = "sometimes"\n')
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_unknown_role_raises(tmp_path):
    with pytest.raises(KeyError):
        load_config(tmp_path).model_for("wizard")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.config'`

- [ ] **Step 3: Implement**

`src/phil/config.py`:

```python
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROLES = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")
DEFAULT_MODEL = "openrouter:poolside/laguna-m.1:free"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleBudget(_Section):
    max_input_tokens: int = 12_000


class RunConfig(_Section):
    tester_mode: Literal["run", "task+run"] = "run"
    max_attempts_per_phase: int = 3
    max_review_rounds: int = 2
    max_tokens: int = 400_000
    max_cost_usd: float = 2.0


class ShellConfig(_Section):
    allow: list[str] = ["pytest*", "uv run *", "npm test*", "git status", "git diff*"]
    timeout_s: int = 300
    max_output_lines: int = 200


class ProjectConfig(_Section):
    test_cmd: str | None = None
    test_globs: list[str] = [
        "tests/*",
        "test/*",
        "test_*.py",
        "*_test.py",
        "*.test.ts",
        "*.spec.ts",
        "*_test.go",
    ]


class PhilConfig(_Section):
    models: dict[str, str] = Field(default_factory=lambda: {role: DEFAULT_MODEL for role in ROLES})
    budget: dict[str, RoleBudget] = {}
    run: RunConfig = RunConfig()
    shell: ShellConfig = ShellConfig()
    project: ProjectConfig = ProjectConfig()

    def model_for(self, role: str) -> str:
        return self.models[role]

    def budget_for(self, role: str) -> RoleBudget:
        return self.budget.get(role, RoleBudget())


def load_config(repo_root: Path) -> PhilConfig:
    path = repo_root / "phil.toml"
    if not path.exists():
        return PhilConfig()
    data = tomllib.loads(path.read_text())
    models = {role: DEFAULT_MODEL for role in ROLES} | data.pop("models", {})
    return PhilConfig(models=models, **data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/config.py tests/test_config.py
git commit -m "$(printf 'Add phil.toml configuration loading\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 4: Repository resolution

**Files:**
- Create: `src/phil/repo.py`
- Test: `tests/test_repo.py`

**Interfaces:**
- Consumes: `git_repo` fixture, `run_git` helper.
- Produces: `phil.repo.RepoError(Exception)`, `NotAGitRepo(RepoError)`, `NoCommits(RepoError)`, frozen dataclass `RepoInfo(root: Path, slug: str, head_sha: str, branch: str | None, dirty_files: list[str])`, `resolve_repo(start: Path) -> RepoInfo`.

- [ ] **Step 1: Write the failing tests**

`tests/test_repo.py`:

```python
import re

import pytest

from phil.repo import NoCommits, NotAGitRepo, resolve_repo
from tests.helpers import run_git


def test_resolves_root_from_subdirectory(git_repo):
    sub = git_repo / "src"
    sub.mkdir()
    info = resolve_repo(sub)
    assert info.root == git_repo
    assert info.branch == "main"
    assert info.head_sha == run_git(git_repo, "rev-parse", "HEAD").strip()


def test_slug_is_name_plus_path_hash(git_repo):
    info = resolve_repo(git_repo)
    assert re.fullmatch(r"target-[0-9a-f]{8}", info.slug)
    assert resolve_repo(git_repo).slug == info.slug


def test_reports_dirty_files(git_repo):
    (git_repo / "app.py").write_text("changed\n")
    (git_repo / "notes.txt").write_text("todo\n")
    info = resolve_repo(git_repo)
    assert info.dirty_files == ["app.py", "notes.txt"]


def test_clean_repo_has_no_dirty_files(git_repo):
    assert resolve_repo(git_repo).dirty_files == []


def test_non_repo_raises(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(NotAGitRepo):
        resolve_repo(plain)


def test_repo_without_commits_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    run_git(empty, "init", "-b", "main")
    with pytest.raises(NoCommits):
        resolve_repo(empty)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.repo'`

- [ ] **Step 3: Implement**

`src/phil/repo.py`:

```python
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepoError(Exception):
    pass


class NotAGitRepo(RepoError):
    pass


class NoCommits(RepoError):
    pass


@dataclass(frozen=True)
class RepoInfo:
    root: Path
    slug: str
    head_sha: str
    branch: str | None
    dirty_files: list[str]


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def resolve_repo(start: Path) -> RepoInfo:
    try:
        root = Path(_git(["rev-parse", "--show-toplevel"], start).strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError) as exc:
        raise NotAGitRepo(f"Not inside a git repository: {start}") from exc
    try:
        head_sha = _git(["rev-parse", "HEAD"], root).strip()
    except subprocess.CalledProcessError as exc:
        raise NoCommits(f"Repository has no commits yet: {root}") from exc
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root).strip()
    status = _git(["status", "--porcelain", "--untracked-files=all"], root)
    dirty = sorted(line[3:] for line in status.splitlines() if line)
    digest = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    return RepoInfo(
        root=root,
        slug=f"{root.name}-{digest}",
        head_sha=head_sha,
        branch=None if branch == "HEAD" else branch,
        dirty_files=dirty,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_repo.py -v`
Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/repo.py tests/test_repo.py
git commit -m "$(printf 'Add git repository resolution\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 5: Store core: paths, database, runs

**Files:**
- Create: `src/phil/store/__init__.py`, `src/phil/store/paths.py`, `src/phil/store/db.py`, `src/phil/store/runs.py`
- Test: `tests/store/__init__.py`, `tests/store/test_runs.py`

**Interfaces:**
- Consumes: `phil_home` fixture.
- Produces:
  - `phil.store.paths.phil_home() -> Path` (reads `PHIL_HOME`, default `~/.phil`); frozen dataclass `ProjectPaths(slug: str, home: Path = phil_home())` with properties `project_dir`, `db_path`, `runs_dir`, `worktrees_dir` and methods `run_dir(run_id: str) -> Path`, `worktree_dir(run_id: str) -> Path`.
  - `phil.store.db.connect(db_path: Path) -> sqlite3.Connection` (autocommit mode, `sqlite3.Row` rows, WAL, schema created); `utcnow() -> str`.
  - `phil.store.runs.RunState` (Literal), dataclass `RunRecord` (columns below), `new_run_id(conn) -> str`, `create_run(conn, *, run_id: str, keyword: str, base_sha: str, worktree: Path, tasks_total: int, story_ref: str | None = None) -> RunRecord`, `get_run(conn, run_id) -> RunRecord | None`, `list_runs(conn) -> list[RunRecord]` (newest first), `update_run(conn, run_id, **fields) -> RunRecord` (raises `ValueError` for unknown fields, `KeyError` for unknown run).

- [ ] **Step 1: Write the failing tests**

`tests/store/__init__.py`: empty file.

`tests/store/test_runs.py`:

```python
import re
from pathlib import Path

import pytest

from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, get_run, list_runs, new_run_id, update_run


@pytest.fixture
def conn(phil_home):
    paths = ProjectPaths("demo-12345678")
    return connect(paths.db_path)


def test_project_paths_live_under_phil_home(phil_home):
    paths = ProjectPaths("demo-12345678")
    assert paths.project_dir == phil_home / "projects" / "demo-12345678"
    assert paths.db_path == paths.project_dir / "phil.db"
    assert paths.run_dir("r-0001") == paths.project_dir / "runs" / "r-0001"
    assert paths.worktree_dir("r-0001") == paths.project_dir / "worktrees" / "r-0001"


def test_connect_creates_db_file(phil_home):
    paths = ProjectPaths("demo-12345678")
    connect(paths.db_path)
    assert paths.db_path.exists()


def test_new_run_id_format(conn):
    assert re.fullmatch(r"r-[0-9a-f]{4}", new_run_id(conn))


def test_create_and_get_run(conn):
    run = create_run(
        conn, run_id="r-0001", keyword="MAPS", base_sha="abc123", worktree=Path("/tmp/wt"), tasks_total=5
    )
    assert run.branch == "phil/r-0001"
    assert run.state == "pending"
    assert run.tasks_done == 0
    assert get_run(conn, "r-0001") == run


def test_get_missing_run_returns_none(conn):
    assert get_run(conn, "r-ffff") is None


def test_list_runs_newest_first(conn):
    for run_id in ("r-0001", "r-0002"):
        create_run(conn, run_id=run_id, keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    assert [run.run_id for run in list_runs(conn)] == ["r-0002", "r-0001"]


def test_update_run_changes_fields(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=3)
    updated = update_run(conn, "r-0001", state="running", current_node="implement", tasks_done=1)
    assert (updated.state, updated.current_node, updated.tasks_done) == ("running", "implement", 1)


def test_update_run_rejects_unknown_fields(conn):
    create_run(conn, run_id="r-0001", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=3)
    with pytest.raises(ValueError):
        update_run(conn, "r-0001", keyword="AUTH")


def test_update_missing_run_raises(conn):
    with pytest.raises(KeyError):
        update_run(conn, "r-ffff", state="running")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/store/test_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.store'`

- [ ] **Step 3: Implement**

`src/phil/store/__init__.py`: empty file.

`src/phil/store/paths.py`:

```python
import os
from dataclasses import dataclass, field
from pathlib import Path


def phil_home() -> Path:
    return Path(os.environ.get("PHIL_HOME", Path.home() / ".phil"))


@dataclass(frozen=True)
class ProjectPaths:
    slug: str
    home: Path = field(default_factory=phil_home)

    @property
    def project_dir(self) -> Path:
        return self.home / "projects" / self.slug

    @property
    def db_path(self) -> Path:
        return self.project_dir / "phil.db"

    @property
    def runs_dir(self) -> Path:
        return self.project_dir / "runs"

    @property
    def worktrees_dir(self) -> Path:
        return self.project_dir / "worktrees"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def worktree_dir(self, run_id: str) -> Path:
        return self.worktrees_dir / run_id
```

`src/phil/store/db.py`:

```python
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    branch TEXT NOT NULL,
    worktree TEXT NOT NULL,
    state TEXT NOT NULL,
    current_node TEXT,
    tasks_done INTEGER NOT NULL DEFAULT 0,
    tasks_total INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    heartbeat_at TEXT,
    needs_attention TEXT,
    story_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    layer TEXT NOT NULL,
    node TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    packet_tokens INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parked (
    id TEXT PRIMARY KEY,
    raised_by TEXT NOT NULL,
    note TEXT NOT NULL,
    why_not_now TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
```

`src/phil/store/runs.py`:

```python
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from phil.store.db import utcnow

RunState = Literal["pending", "running", "paused", "escalated", "completed", "failed", "aborted"]

_UPDATABLE = {
    "state",
    "current_node",
    "tasks_done",
    "tasks_total",
    "pid",
    "heartbeat_at",
    "needs_attention",
}


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    keyword: str
    base_sha: str
    branch: str
    worktree: str
    state: str
    current_node: str | None
    tasks_done: int
    tasks_total: int
    pid: int | None
    heartbeat_at: str | None
    needs_attention: str | None
    story_ref: str | None
    created_at: str
    updated_at: str


def new_run_id(conn: sqlite3.Connection) -> str:
    while True:
        run_id = f"r-{secrets.token_hex(2)}"
        if get_run(conn, run_id) is None:
            return run_id


def create_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    keyword: str,
    base_sha: str,
    worktree: Path,
    tasks_total: int,
    story_ref: str | None = None,
) -> RunRecord:
    now = utcnow()
    conn.execute(
        "INSERT INTO runs (run_id, keyword, base_sha, branch, worktree, state, tasks_total,"
        " story_ref, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
        (run_id, keyword, base_sha, f"phil/{run_id}", str(worktree), tasks_total, story_ref, now, now),
    )
    run = get_run(conn, run_id)
    assert run is not None
    return run


def get_run(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return RunRecord(**dict(row)) if row else None


def list_runs(conn: sqlite3.Connection) -> list[RunRecord]:
    rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC").fetchall()
    return [RunRecord(**dict(row)) for row in rows]


def update_run(conn: sqlite3.Connection, run_id: str, **fields: object) -> RunRecord:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"cannot update run fields: {sorted(unknown)}")
    assignments = ", ".join(f"{name} = ?" for name in fields)
    cursor = conn.execute(
        f"UPDATE runs SET {assignments}, updated_at = ? WHERE run_id = ?",
        (*fields.values(), utcnow(), run_id),
    )
    if cursor.rowcount == 0:
        raise KeyError(run_id)
    run = get_run(conn, run_id)
    assert run is not None
    return run
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/test_runs.py -v`
Expected: all 9 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/store tests/store
git commit -m "$(printf 'Add SQLite store with project paths and runs table\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 6: Store: telemetry and parking lot

**Files:**
- Create: `src/phil/store/telemetry.py`, `src/phil/store/parked.py`
- Test: `tests/store/test_telemetry_parked.py`

**Interfaces:**
- Consumes: `connect()` from Task 5; `ParkedItem`, `Ref` from Task 2.
- Produces:
  - `phil.store.telemetry.TelemetryRow` (pydantic: `run_id: str | None, layer: Literal["chat","run"], node, role, model: str, attempt, packet_tokens, input_tokens, output_tokens, latency_ms: int, cost_usd: float, outcome: Literal["ok","invalid","evidence_fail","error"]`), `record(conn, row) -> None`, dataclass `UsageLine(layer, role, calls, input_tokens, output_tokens, cost_usd)`, `usage_by_role(conn, run_id) -> list[UsageLine]`, `run_totals(conn, run_id) -> tuple[int, float]` (total tokens, total cost).
  - `phil.store.parked.park(conn, *, raised_by: str, note: str, why_not_now: str, source: Ref, run_id: str | None = None) -> ParkedItem` (ids `P-001`, `P-002`, …), `list_parked(conn, status: str | None = "open") -> list[ParkedItem]`, `set_parked_status(conn, item_id: str, status: str) -> ParkedItem` (`KeyError` if missing), `open_count(conn) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/store/test_telemetry_parked.py`:

```python
import pytest

from phil.contracts import Ref
from phil.store.db import connect
from phil.store.parked import list_parked, open_count, park, set_parked_status
from phil.store.paths import ProjectPaths
from phil.store.telemetry import TelemetryRow, record, run_totals, usage_by_role


@pytest.fixture
def conn(phil_home):
    return connect(ProjectPaths("demo-12345678").db_path)


def row(**overrides) -> TelemetryRow:
    values = dict(
        run_id="r-0001",
        layer="run",
        node="implement",
        role="implementer",
        model="m",
        attempt=1,
        packet_tokens=900,
        input_tokens=1000,
        output_tokens=200,
        latency_ms=1500,
        cost_usd=0.01,
        outcome="ok",
    )
    return TelemetryRow(**(values | overrides))


def test_usage_groups_by_layer_and_role(conn):
    record(conn, row())
    record(conn, row(attempt=2, outcome="invalid"))
    record(conn, row(node="review", role="reviewer", input_tokens=3000, output_tokens=500, cost_usd=0.05))
    record(conn, row(run_id=None, layer="chat", role="orchestrator"))
    lines = usage_by_role(conn, "r-0001")
    assert [(line.layer, line.role, line.calls) for line in lines] == [
        ("run", "implementer", 2),
        ("run", "reviewer", 1),
    ]
    implementer = lines[0]
    assert (implementer.input_tokens, implementer.output_tokens) == (2000, 400)


def test_run_totals(conn):
    record(conn, row())
    record(conn, row(cost_usd=0.02))
    tokens, cost = run_totals(conn, "r-0001")
    assert tokens == 2400
    assert cost == pytest.approx(0.03)


def test_run_totals_for_unknown_run_is_zero(conn):
    assert run_totals(conn, "r-ffff") == (0, 0.0)


def test_park_assigns_sequential_ids(conn):
    source = Ref(label="reviewer note", path="runs/r-0001/outputs/review-run-1.json")
    first = park(conn, raised_by="reviewer", note="N+1 query in listings", why_not_now="not in MAPS", source=source)
    second = park(conn, raised_by="user", note="dark mode", why_not_now="later", source=source)
    assert (first.id, second.id) == ("P-001", "P-002")
    assert first.status == "open"


def test_list_and_count_open_items(conn):
    source = Ref(label="x", path="y")
    park(conn, raised_by="user", note="a", why_not_now="b", source=source)
    item = park(conn, raised_by="user", note="c", why_not_now="d", source=source)
    set_parked_status(conn, item.id, "dropped")
    assert [p.note for p in list_parked(conn)] == ["a"]
    assert len(list_parked(conn, status=None)) == 2
    assert open_count(conn) == 1


def test_set_status_on_missing_item_raises(conn):
    with pytest.raises(KeyError):
        set_parked_status(conn, "P-999", "dropped")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/store/test_telemetry_parked.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.store.telemetry'`

- [ ] **Step 3: Implement**

`src/phil/store/telemetry.py`:

```python
import sqlite3
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from phil.store.db import utcnow


class TelemetryRow(BaseModel):
    run_id: str | None
    layer: Literal["chat", "run"]
    node: str
    role: str
    model: str
    attempt: int
    packet_tokens: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    outcome: Literal["ok", "invalid", "evidence_fail", "error"]


@dataclass(frozen=True)
class UsageLine:
    layer: str
    role: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def record(conn: sqlite3.Connection, row: TelemetryRow) -> None:
    data = row.model_dump() | {"created_at": utcnow()}
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    conn.execute(f"INSERT INTO telemetry ({columns}) VALUES ({placeholders})", tuple(data.values()))


def usage_by_role(conn: sqlite3.Connection, run_id: str) -> list[UsageLine]:
    rows = conn.execute(
        "SELECT layer, role, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
        " SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost_usd"
        " FROM telemetry WHERE run_id = ? GROUP BY layer, role ORDER BY layer, role",
        (run_id,),
    ).fetchall()
    return [UsageLine(**dict(row)) for row in rows]


def run_totals(conn: sqlite3.Connection, run_id: str) -> tuple[int, float]:
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,"
        " COALESCE(SUM(cost_usd), 0.0) AS cost FROM telemetry WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row["tokens"]), float(row["cost"])
```

`src/phil/store/parked.py`:

```python
import sqlite3

from phil.contracts import ParkedItem, Ref
from phil.store.db import utcnow


def _to_item(row: sqlite3.Row) -> ParkedItem:
    return ParkedItem(
        id=row["id"],
        raised_by=row["raised_by"],
        note=row["note"],
        why_not_now=row["why_not_now"],
        source=Ref(label=row["source_label"], path=row["source_path"]),
        status=row["status"],
        run_id=row["run_id"],
    )


def park(
    conn: sqlite3.Connection,
    *,
    raised_by: str,
    note: str,
    why_not_now: str,
    source: Ref,
    run_id: str | None = None,
) -> ParkedItem:
    conn.execute("BEGIN IMMEDIATE")
    try:
        count = conn.execute("SELECT COUNT(*) FROM parked").fetchone()[0]
        item_id = f"P-{count + 1:03d}"
        conn.execute(
            "INSERT INTO parked (id, raised_by, note, why_not_now, source_label, source_path,"
            " status, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (item_id, raised_by, note, why_not_now, source.label, source.path, run_id, utcnow()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _get(conn, item_id)


def _get(conn: sqlite3.Connection, item_id: str) -> ParkedItem:
    row = conn.execute("SELECT * FROM parked WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    return _to_item(row)


def list_parked(conn: sqlite3.Connection, status: str | None = "open") -> list[ParkedItem]:
    if status is None:
        rows = conn.execute("SELECT * FROM parked ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM parked WHERE status = ? ORDER BY id", (status,)).fetchall()
    return [_to_item(row) for row in rows]


def set_parked_status(conn: sqlite3.Connection, item_id: str, status: str) -> ParkedItem:
    cursor = conn.execute("UPDATE parked SET status = ? WHERE id = ?", (status, item_id))
    if cursor.rowcount == 0:
        raise KeyError(item_id)
    return _get(conn, item_id)


def open_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM parked WHERE status = 'open'").fetchone()[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/test_telemetry_parked.py -v`
Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/store/telemetry.py src/phil/store/parked.py tests/store/test_telemetry_parked.py
git commit -m "$(printf 'Add telemetry and parking lot tables\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 7: Run artifacts on disk

**Files:**
- Create: `src/phil/store/artifacts.py`
- Test: `tests/store/test_artifacts.py`

**Interfaces:**
- Consumes: `Plan`, `Task`, `Contract` from Task 2.
- Produces: `phil.store.artifacts.artifact_name(node: str, task_id: str | None, attempt: int) -> str` (e.g. `implement-MAPS-001-2`, or `review-run-1` when `task_id` is None); class `ArtifactStore(run_dir: Path)` with `write_plan(plan: Plan) -> Path`, `read_plan() -> Plan`, `write(subdir: str, name: str, contract: BaseModel) -> Path`, `read(path: Path, model: type[T]) -> T`, `append_assumptions(*, node: str, task_id: str | None, assumptions: list[str]) -> None`, `read_assumptions() -> list[dict]`, `write_log(name: str, text: str) -> Path`.

- [ ] **Step 1: Write the failing tests**

`tests/store/test_artifacts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/store/test_artifacts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.store.artifacts'`

- [ ] **Step 3: Implement**

`src/phil/store/artifacts.py`:

```python
import json
from pathlib import Path

from pydantic import BaseModel

from phil.contracts import Plan


def artifact_name(node: str, task_id: str | None, attempt: int) -> str:
    return f"{node}-{task_id or 'run'}-{attempt}"


class ArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def _file(self, relative: str) -> Path:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_plan(self, plan: Plan) -> Path:
        path = self._file("plan.json")
        path.write_text(plan.model_dump_json(indent=2))
        return path

    def read_plan(self) -> Plan:
        return Plan.model_validate_json((self.run_dir / "plan.json").read_text())

    def write(self, subdir: str, name: str, contract: BaseModel) -> Path:
        path = self._file(f"{subdir}/{name}.json")
        path.write_text(contract.model_dump_json(indent=2))
        return path

    def read[T: BaseModel](self, path: Path, model: type[T]) -> T:
        return model.model_validate_json(path.read_text())

    def append_assumptions(self, *, node: str, task_id: str | None, assumptions: list[str]) -> None:
        path = self._file("assumptions.jsonl")
        with path.open("a") as handle:
            for assumption in assumptions:
                entry = {"node": node, "task_id": task_id, "assumption": assumption, "status": "open"}
                handle.write(json.dumps(entry) + "\n")

    def read_assumptions(self) -> list[dict]:
        path = self.run_dir / "assumptions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def write_log(self, name: str, text: str) -> Path:
        path = self._file(f"logs/{name}.log")
        path.write_text(text)
        return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store/test_artifacts.py -v`
Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/store/artifacts.py tests/store/test_artifacts.py
git commit -m "$(printf 'Add on-disk run artifact store and assumption ledger\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 8: Git worktree manager

**Files:**
- Create: `src/phil/workspace/__init__.py`, `src/phil/workspace/worktree.py`
- Test: `tests/workspace/__init__.py`, `tests/workspace/test_worktree.py`

**Interfaces:**
- Consumes: `git_repo` fixture, `run_git` helper.
- Produces: frozen dataclass `Worktree(path: Path, branch: str, base_sha: str)`; class `WorktreeManager(repo_root: Path)` with `create(*, run_id: str, base_sha: str, path: Path) -> Worktree`, `remove(worktree: Worktree, *, delete_branch: bool = True) -> None`, `changed_files(path: Path, since: str) -> list[str]` (tracked changes vs `since` including uncommitted, plus untracked; sorted, unique), `commit_all(path: Path, message: str) -> str` (new HEAD sha), `diff(path: Path, base: str) -> str` (`git diff base HEAD`), `head(path: Path) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/workspace/__init__.py`: empty file.

`tests/workspace/test_worktree.py`:

```python
import pytest

from phil.workspace.worktree import WorktreeManager
from tests.helpers import run_git


@pytest.fixture
def setup(git_repo, tmp_path):
    manager = WorktreeManager(git_repo)
    base = run_git(git_repo, "rev-parse", "HEAD").strip()
    worktree = manager.create(run_id="r-0001", base_sha=base, path=tmp_path / "wt" / "r-0001")
    return manager, worktree, base


def test_create_makes_branch_and_checkout(setup, git_repo):
    manager, worktree, base = setup
    assert worktree.branch == "phil/r-0001"
    assert (worktree.path / "app.py").exists()
    assert "phil/r-0001" in run_git(git_repo, "branch", "--list", "phil/r-0001")
    assert manager.head(worktree.path) == base


def test_user_checkout_is_untouched(setup, git_repo):
    manager, worktree, _ = setup
    (worktree.path / "app.py").write_text("changed in worktree\n")
    assert run_git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert (git_repo / "app.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_changed_files_includes_modified_and_untracked(setup):
    manager, worktree, base = setup
    (worktree.path / "app.py").write_text("changed\n")
    (worktree.path / "tests").mkdir()
    (worktree.path / "tests" / "test_app.py").write_text("def test_x(): pass\n")
    assert manager.changed_files(worktree.path, since=base) == ["app.py", "tests/test_app.py"]


def test_commit_all_advances_branch(setup, git_repo):
    manager, worktree, base = setup
    (worktree.path / "new.py").write_text("x = 1\n")
    sha = manager.commit_all(worktree.path, "MAPS-001: add new")
    assert sha != base
    assert run_git(git_repo, "rev-parse", "phil/r-0001").strip() == sha
    assert "new.py" in manager.diff(worktree.path, base)
    assert manager.changed_files(worktree.path, since=sha) == []


def test_remove_deletes_worktree_and_branch(setup, git_repo):
    manager, worktree, _ = setup
    manager.remove(worktree)
    assert not worktree.path.exists()
    assert run_git(git_repo, "branch", "--list", "phil/r-0001").strip() == ""


def test_remove_can_keep_branch(setup, git_repo):
    manager, worktree, _ = setup
    manager.remove(worktree, delete_branch=False)
    assert not worktree.path.exists()
    assert "phil/r-0001" in run_git(git_repo, "branch", "--list", "phil/r-0001")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/workspace/test_worktree.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.workspace'`

- [ ] **Step 3: Implement**

`src/phil/workspace/__init__.py`: empty file.

`src/phil/workspace/worktree.py`:

```python
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_sha: str


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


class WorktreeManager:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def create(self, *, run_id: str, base_sha: str, path: Path) -> Worktree:
        branch = f"phil/{run_id}"
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo_root, "worktree", "add", "-b", branch, str(path), base_sha)
        return Worktree(path=path, branch=branch, base_sha=base_sha)

    def remove(self, worktree: Worktree, *, delete_branch: bool = True) -> None:
        _git(self.repo_root, "worktree", "remove", "--force", str(worktree.path))
        if delete_branch:
            _git(self.repo_root, "branch", "-D", worktree.branch)

    def changed_files(self, path: Path, since: str) -> list[str]:
        tracked = _git(path, "diff", "--name-only", since).splitlines()
        untracked = _git(path, "ls-files", "--others", "--exclude-standard").splitlines()
        return sorted(set(tracked) | set(untracked))

    def commit_all(self, path: Path, message: str) -> str:
        _git(path, "add", "-A")
        _git(path, "commit", "-m", message)
        return self.head(path)

    def diff(self, path: Path, base: str) -> str:
        return _git(path, "diff", base, "HEAD")

    def head(self, path: Path) -> str:
        return _git(path, "rev-parse", "HEAD").strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/workspace/test_worktree.py -v`
Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/workspace tests/workspace
git commit -m "$(printf 'Add git worktree manager\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 9: Shell policy, runner, and output truncation

**Files:**
- Create: `src/phil/workspace/shell.py`
- Test: `tests/workspace/test_shell.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ShellPolicy(allow: list[str])` with `is_allowed(command: str) -> bool` (glob match via `fnmatch.fnmatchcase`; rejects shell metacharacters `; & | $ \` < >` and newlines); frozen dataclass `ShellResult(command: str, exit_code: int, stdout: str, stderr: str, timed_out: bool, duration_ms: int)` with property `ok: bool`; `run_command(command: str, cwd: Path, timeout_s: float) -> ShellResult` (no shell; `shlex.split`; kills the whole process group on timeout; exit code 127 when the program is not found; `-9` on timeout); `truncate_output(text: str, max_lines: int, keep: tuple[str, ...] = ("FAIL", "Error", "error", "assert")) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/workspace/test_shell.py`:

```python
import shlex
import sys

import pytest

from phil.workspace.shell import ShellPolicy, run_command, truncate_output

PY = shlex.quote(sys.executable)


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("pytest -q", True),
        ("git status", True),
        ("git push", False),
        ("pytest; rm -rf /", False),
        ("pytest && echo hi", False),
        ("pytest | tee out", False),
        ("echo $(whoami)", False),
        ("pytest > out.txt", False),
    ],
)
def test_policy(command, allowed):
    assert ShellPolicy(["pytest*", "git status"]).is_allowed(command) is allowed


def test_run_command_captures_output(tmp_path):
    result = run_command(f"{PY} -c \"print('hi')\"", cwd=tmp_path, timeout_s=10)
    assert result.ok
    assert result.stdout.strip() == "hi"
    assert result.exit_code == 0


def test_run_command_nonzero_exit(tmp_path):
    result = run_command(f'{PY} -c "import sys; sys.exit(3)"', cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 3
    assert not result.ok


def test_run_command_missing_program(tmp_path):
    result = run_command("definitely-not-a-real-program-xyz", cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 127
    assert not result.ok


def test_run_command_times_out(tmp_path):
    result = run_command(f'{PY} -c "import time; time.sleep(10)"', cwd=tmp_path, timeout_s=0.5)
    assert result.timed_out
    assert not result.ok
    assert result.duration_ms < 5000


def test_truncate_short_text_unchanged():
    assert truncate_output("a\nb\nc", max_lines=10) == "a\nb\nc"


def test_truncate_keeps_head_tail_and_failures():
    lines = [f"line {i}" for i in range(1000)]
    lines[500] = "FAILED tests/test_map.py::test_markers"
    out = truncate_output("\n".join(lines), max_lines=40).splitlines()
    assert out[0] == "line 0"
    assert out[-1] == "line 999"
    assert "FAILED tests/test_map.py::test_markers" in out
    assert any("lines omitted" in line for line in out)
    assert len(out) <= 41
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/workspace/test_shell.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.workspace.shell'`

- [ ] **Step 3: Implement**

`src/phil/workspace/shell.py`:

```python
import fnmatch
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_FORBIDDEN = set(";&|$`<>\n")


class ShellPolicy:
    def __init__(self, allow: list[str]) -> None:
        self.allow = allow

    def is_allowed(self, command: str) -> bool:
        command = command.strip()
        if _FORBIDDEN & set(command):
            return False
        return any(fnmatch.fnmatchcase(command, pattern) for pattern in self.allow)


@dataclass(frozen=True)
class ShellResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def run_command(command: str, cwd: Path, timeout_s: float) -> ShellResult:
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        proc = subprocess.Popen(
            shlex.split(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return ShellResult(command, 127, "", str(exc), False, elapsed())
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        return ShellResult(command, proc.returncode, stdout, stderr, False, elapsed())
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        return ShellResult(command, -9, stdout, stderr, True, elapsed())


def truncate_output(
    text: str,
    max_lines: int,
    keep: tuple[str, ...] = ("FAIL", "Error", "error", "assert"),
) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head_n = max_lines // 4
    tail_n = max_lines // 2
    middle_budget = max_lines - head_n - tail_n
    middle = lines[head_n : len(lines) - tail_n]
    hits = [line for line in middle if any(marker in line for marker in keep)][:middle_budget]
    omitted = len(middle) - len(hits)
    marker = f"... [{omitted} lines omitted; full log saved] ..."
    return "\n".join([*lines[:head_n], marker, *hits, *lines[len(lines) - tail_n :]])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/workspace/test_shell.py -v`
Expected: all 14 PASS

- [ ] **Step 5: Commit**

```bash
git add src/phil/workspace/shell.py tests/workspace/test_shell.py
git commit -m "$(printf 'Add shell allowlist policy, runner, and output truncation\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 10: Themed console and CLI commands (`runs`, `parked`, `schema`, `--repo`)

**Files:**
- Create: `src/phil/ui/__init__.py`, `src/phil/ui/theme.py`, `tests/test_ui.py`
- Modify: `src/phil/cli/main.py` (full replacement below), `tests/test_cli.py` (append tests)

**Interfaces:**
- Consumes: `resolve_repo`, `RepoError` (Task 4); `ProjectPaths`, `connect` (Task 5); `list_runs`, `create_run` (Task 5); `run_totals` (Task 6); `list_parked`, `park` (Task 6); `export_schemas`, `ALL_CONTRACTS` (Task 2).
- Produces: `phil.ui.theme.PHIL_THEME: rich.theme.Theme`, `STYLE_NAMES: tuple[str, ...]`, `make_console(**kwargs) -> rich.console.Console`; CLI commands `phil runs`, `phil parked`, `phil schema [--out DIR]`, global option `--repo PATH`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ui.py`:

```python
from phil.ui.theme import STYLE_NAMES, make_console


def test_all_styles_are_namespaced():
    assert STYLE_NAMES
    assert all(name.startswith("phil.") for name in STYLE_NAMES)


def test_console_renders_theme_styles():
    console = make_console(record=True, width=80, force_terminal=False)
    for name in STYLE_NAMES:
        console.print(f"[{name}]sample[/]")
    assert console.export_text().count("sample") == len(STYLE_NAMES)
```

Append to `tests/test_cli.py`:

```python
import subprocess
import sys
from pathlib import Path

from phil.contracts import ALL_CONTRACTS, Ref
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.parked import park
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run


def _conn_for(repo: Path):
    return connect(ProjectPaths(resolve_repo(repo).slug).db_path)


def test_runs_empty(git_repo):
    result = runner.invoke(app, ["--repo", str(git_repo), "runs"])
    assert result.exit_code == 0
    assert "No runs yet" in result.output


def test_runs_lists_runs(git_repo):
    create_run(
        _conn_for(git_repo), run_id="r-7f3a", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=5
    )
    result = runner.invoke(app, ["--repo", str(git_repo), "runs"])
    assert result.exit_code == 0
    assert "r-7f3a" in result.output
    assert "MAPS" in result.output
    assert "0/5" in result.output


def test_runs_outside_repo_fails(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = runner.invoke(app, ["--repo", str(plain), "runs"])
    assert result.exit_code == 1
    assert "Not inside a git repository" in result.output


def test_parked_lists_open_items(git_repo):
    park(
        _conn_for(git_repo),
        raised_by="reviewer",
        note="N+1 query in listings",
        why_not_now="out of scope",
        source=Ref(label="review", path="x"),
    )
    result = runner.invoke(app, ["--repo", str(git_repo), "parked"])
    assert result.exit_code == 0
    assert "P-001" in result.output
    assert "N+1 query" in result.output


def test_schema_command_exports_all(tmp_path):
    out = tmp_path / "schemas"
    result = runner.invoke(app, ["schema", "--out", str(out)])
    assert result.exit_code == 0
    assert len(list(out.glob("*.schema.json"))) == len(ALL_CONTRACTS)


def test_cli_import_does_not_load_llm_stack():
    code = (
        "import sys, phil.cli.main; "
        "heavy = ('langchain', 'langgraph', 'deepagents', 'langchain_openrouter'); "
        "print(','.join(m for m in heavy if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.ui'` and `No such command 'runs'`.

- [ ] **Step 3: Implement the theme**

`src/phil/ui/__init__.py`: empty file.

`src/phil/ui/theme.py`:

```python
from typing import Any

from rich.console import Console
from rich.theme import Theme

PHIL_THEME = Theme(
    {
        "phil.brand": "bold cyan",
        "phil.agent": "cyan",
        "phil.user": "bold white",
        "phil.muted": "dim",
        "phil.id": "bold blue",
        "phil.warn": "yellow",
        "phil.error": "bold red",
        "phil.gate.pass": "green",
        "phil.gate.fail": "red",
        "phil.cost": "magenta",
    }
)

STYLE_NAMES: tuple[str, ...] = tuple(name for name in PHIL_THEME.styles if name.startswith("phil."))


def make_console(**kwargs: Any) -> Console:
    return Console(theme=PHIL_THEME, **kwargs)
```

- [ ] **Step 4: Implement the CLI commands**

Replace `src/phil/cli/main.py` with:

```python
import sqlite3
from pathlib import Path

import typer
from rich.table import Table

from phil import __version__
from phil.contracts.schema import export_schemas
from phil.repo import RepoError, RepoInfo, resolve_repo
from phil.store.db import connect
from phil.store.parked import list_parked
from phil.store.paths import ProjectPaths
from phil.store.runs import list_runs
from phil.store.telemetry import run_totals
from phil.ui.theme import make_console

app = typer.Typer(add_completion=False, help="Phil: a contract-driven coding agent.")
console = make_console()


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Show version and exit."
    ),
    repo: Path | None = typer.Option(None, "--repo", help="Target repository (default: current directory)."),
) -> None:
    ctx.obj = {"repo": repo}
    if ctx.invoked_subcommand is None:
        console.print("[phil.muted]Chat mode is not implemented yet. Try `phil runs`.[/]")


def _open_project(ctx: typer.Context) -> tuple[RepoInfo, sqlite3.Connection]:
    start = ctx.obj.get("repo") or Path.cwd()
    try:
        info = resolve_repo(start)
    except RepoError as exc:
        console.print(f"[phil.error]{exc}[/]")
        raise typer.Exit(1) from exc
    return info, connect(ProjectPaths(info.slug).db_path)


def _format_tokens(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


@app.command()
def runs(ctx: typer.Context) -> None:
    """List runs for the current repository."""
    _, conn = _open_project(ctx)
    records = list_runs(conn)
    if not records:
        console.print("[phil.muted]No runs yet.[/]")
        return
    table = Table(box=None, pad_edge=False)
    for column in ("run", "plan", "done", "state", "tokens", "cost"):
        table.add_column(column, style="phil.muted", no_wrap=True)
    for run in records:
        tokens, cost = run_totals(conn, run.run_id)
        table.add_row(
            f"[phil.id]{run.run_id}[/]",
            run.keyword,
            f"{run.tasks_done}/{run.tasks_total}",
            run.state,
            _format_tokens(tokens),
            f"[phil.cost]${cost:.2f}[/]",
        )
    console.print(table)


@app.command()
def parked(ctx: typer.Context) -> None:
    """List open parking-lot items for the current repository."""
    _, conn = _open_project(ctx)
    items = list_parked(conn)
    if not items:
        console.print("[phil.muted]Parking lot is empty.[/]")
        return
    for item in items:
        console.print(f"[phil.id]{item.id}[/] {item.note} [phil.muted]({item.raised_by}: {item.why_not_now})[/]")


@app.command()
def schema(out: Path = typer.Option(Path("phil-schemas"), "--out", help="Output directory.")) -> None:
    """Export JSON Schema for every contract."""
    paths = export_schemas(out)
    console.print(f"Wrote [phil.id]{len(paths)}[/] schemas to {out}")
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests PASS (Tasks 1–10).

Run: `cd /tmp && uv run --project ~/Code/phil phil runs; cd -`
Expected: `Not inside a git repository: /tmp` (or similar path) and exit code 1.

Run: `uv run phil runs`
Expected: `No runs yet.` (this repo has no runs).

- [ ] **Step 6: Commit**

```bash
git add src/phil/ui src/phil/cli/main.py tests/test_ui.py tests/test_cli.py
git commit -m "$(printf 'Add themed console and runs, parked, schema commands\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

## Spec coverage for this plan

| Spec section | Covered here | Deferred to |
|---|---|---|
| §3 UX: repo resolution, `--repo`, base, scoped commands | Tasks 4, 10 | Dirty-file warning display: plan 4 |
| §3 Terminal presentation via `ui/` theme | Task 10 | Branding: future |
| §4.1 Components: `cli/`, `contracts/`, `store/`, `workspace/`, `ui/`, `config.py` | Tasks 1–10 | `chat/`, `interface/`, `agents/`, `packets/`, `run/`: plans 2–4 |
| §4.4 State location | Tasks 5, 7 | LangGraph checkpoints in `phil.db`: plan 3 |
| §5 Contracts, JSON Schema export | Task 2 | `Feedback`, `ImplementInput`, other packet inputs: plans 2 and 4 |
| §7 Worktree, per-task commit primitives | Task 8 | Gates and graph: plan 3 |
| §9 Config budgets, shell allowlist, output offloading | Tasks 3, 7, 9 | Packet builders: plan 2 |
| §9a Parking lot storage, `phil parked` | Tasks 6, 10 | `/park`, `present()`: plan 4 |
| §10 Shell timeouts and process-group kill | Task 9 | Interrupt on non-allowlisted command: plan 3 |
| §11 Telemetry table and totals | Task 6 | Recording from `invoke_agent`: plan 2; `--usage` view: plan 3 |
| §12 Unit tests, lazy-import check | All tasks, Task 10 | Graph and live tests: plans 3 and 4 |
