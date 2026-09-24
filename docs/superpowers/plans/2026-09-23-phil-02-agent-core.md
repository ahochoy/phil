# Phil Plan 2: Agent Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the layer that turns a context packet into a validated contract: `AgentSpec` and role registry, prompts, context packets with token budgets, a policy-checked shell tool, `invoke_agent` (contract validation with one retry, evidence checks, telemetry, assumption ledger, parking lot, provider retries), a `FakeAgent` for tests, and the real deepagents factory, plus the four items that had to land before any of this.

**Architecture:** `invoke_agent(spec, packet, ctx)` is the only place a model is called (spec §4.2). It gets its agent from a factory, so tests use `FakeAgentFactory` and production uses `build_deep_agent`, which is the only code that imports `deepagents`/LangChain (inside the function body). Agents receive a rendered packet as a single user message and return a Pydantic contract through `response_format`.

**Tech Stack:** Python 3.14, pydantic 2, deepagents 0.5.6 (`create_deep_agent`, `FilesystemBackend`, `FilesystemPermission`), langchain-openrouter 0.2.3 (usage in `AIMessage.usage_metadata`, cost in `response_metadata["cost"]`), sqlite3, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-phil-v1-design.md`
**Follow-ups consumed:** `docs/superpowers/plans/2026-09-23-phil-01-followups.md` ("Before plan 2" section)

**Plan series:** 1 Foundation (done) → **2 Agent core (this plan)** → 3 Run graph → 4 Chat and interface → MVP lifecycle (PR + cleanup).

## Global Constraints

- Python `>=3.14`; `uv` for dependencies; source in `src/phil/`, tests in `tests/`; run `uv run pytest`.
- Every contract inherits `Contract` (top level, `schema_version`) or `Part` (nested). Both forbid extra fields.
- Only `phil.agents.factory.build_deep_agent` may import `deepagents`, `langchain`, `langchain_core`, `langgraph`, or `langchain_openrouter`, and only inside its function body. Importing `phil.cli.main`, `phil.agents.invoke`, or `phil.packets` must not load them.
- `invoke_agent` is the only code path that calls a model.
- Default model for every role: `openrouter:poolside/laguna-m.1:free` (`phil.config.DEFAULT_MODEL`). Do not change it.
- Child processes never receive secret-looking environment variables unless listed in `[shell] pass_env`.
- Tests never touch the real `~/.phil` (autouse `phil_home` fixture) and never call a real model except tests marked `live`.
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` after a blank line.

## File Structure

```
src/phil/git.py                      modify: GitError stores .command, pickles cleanly
src/phil/store/db.py                 modify: MIGRATIONS + PRAGMA user_version
src/phil/config.py                   modify: ShellConfig.pass_env
src/phil/workspace/shell.py          modify: is_secret_name(), child_env(), run_command(env=)
src/phil/contracts/common.py         modify: field descriptions
src/phil/contracts/planning.py       modify: field descriptions
src/phil/contracts/results.py        modify: field descriptions
src/phil/contracts/inputs.py         create: ArchitectInput, CriticInput, ImplementInput, TesterInput, ReviewInput
src/phil/contracts/__init__.py       modify: export inputs, extend ALL_CONTRACTS
src/phil/packets/__init__.py         create: re-exports
src/phil/packets/packet.py           create: Packet, PacketTooLarge, estimate_tokens(), build_packet()
src/phil/prompts/__init__.py         create: empty (package for importlib.resources)
src/phil/prompts/_shared.md          create: output discipline + self-check block
src/phil/prompts/{architect,critic,implementer,tester,reviewer}.md   create
src/phil/agents/__init__.py          create: empty
src/phil/agents/spec.py              create: AgentSpec, load_prompt()
src/phil/agents/registry.py          create: SPECS, get_spec()
src/phil/agents/tools.py             create: CommandLog, make_shell_tool()
src/phil/agents/usage.py             create: Usage, extract_usage()
src/phil/agents/fake.py              create: FakeMessage, FakeAgent, FakeAgentFactory
src/phil/agents/evidence.py          create: check_evidence()
src/phil/agents/retry.py             create: is_transient(), call_with_retry()
src/phil/agents/invoke.py            create: AgentContext, ContractViolation, invoke_agent()
src/phil/agents/factory.py           create: build_deep_agent()
tests/agents/__init__.py, tests/agents/*.py, tests/packets/__init__.py, tests/packets/test_packet.py
tests/live/__init__.py, tests/live/test_invoke_live.py
docs/superpowers/specs/2026-09-23-phil-v1-design.md   modify (Task 3): secret-name rule wording
```

---

### Task 1: `GitError` stores `.command` and pickles cleanly

**Files:**
- Modify: `src/phil/git.py` (the `GitError` class)
- Test: `tests/test_git.py` (append)

**Interfaces:**
- Consumes: existing `phil.git.git`, `GitNotFound(GitError)`.
- Produces: `GitError(command: list[str], returncode: int, stderr: str)` with attributes `.command: list[str]`, `.returncode: int`, `.stderr: str`; `exc.args == (command, returncode, stderr)`; picklable. `str()` unchanged in content.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_git.py`:

```python
import pickle


def test_git_error_exposes_command(git_repo):
    with pytest.raises(GitError) as excinfo:
        git(git_repo, "not-a-real-subcommand")
    assert excinfo.value.command == ["not-a-real-subcommand"]


def test_git_error_round_trips_through_pickle():
    error = GitError(["status"], 128, "fatal: not a git repository")
    restored = pickle.loads(pickle.dumps(error))
    assert restored.command == ["status"]
    assert restored.returncode == 128
    assert restored.stderr == "fatal: not a git repository"
    assert str(restored) == str(error)


def test_git_error_repr_keeps_details():
    assert "128" in repr(GitError(["status"], 128, "boom"))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_git.py -v`
Expected: `test_git_error_exposes_command` fails with `AttributeError: 'GitError' object has no attribute 'command'`; the pickle test fails with `TypeError`.

- [ ] **Step 3: Implement** — replace the `GitError` class in `src/phil/git.py` with:

```python
class GitError(Exception):
    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        super().__init__(command, returncode, stderr)
        self.command = list(command)
        self.returncode = returncode
        self.stderr = stderr

    def __str__(self) -> str:
        return f"git {' '.join(self.command)} failed (exit {self.returncode}): {self.stderr}"
```

`git()` and `GitNotFound` stay as they are (they already pass `list(args), returncode, stderr` positionally).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_git.py tests/test_repo.py tests/workspace/test_worktree.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/git.py tests/test_git.py
git commit -m "$(printf 'Store git command on GitError so it pickles cleanly\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 2: Database schema versioning

**Files:**
- Modify: `src/phil/store/db.py`
- Test: `tests/store/test_db.py` (create)

**Interfaces:**
- Consumes: existing `SCHEMA` string and `connect()` in `src/phil/store/db.py`.
- Produces: `phil.store.db.MIGRATIONS: list[str]` (index `i` upgrades a database from version `i` to `i + 1`; `MIGRATIONS[0] == SCHEMA`); `connect()` applies pending migrations so `PRAGMA user_version == len(MIGRATIONS)` afterwards. Future plans add schema changes by appending to `MIGRATIONS`, never by editing earlier entries.

- [ ] **Step 1: Write the failing tests** — `tests/store/test_db.py`:

```python
import sqlite3

from phil.store import db
from phil.store.db import MIGRATIONS, connect


def user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_new_database_is_at_latest_version(tmp_path):
    conn = connect(tmp_path / "phil.db")
    assert user_version(conn) == len(MIGRATIONS)


def test_connect_is_idempotent(tmp_path):
    connect(tmp_path / "phil.db")
    conn = connect(tmp_path / "phil.db")
    assert user_version(conn) == len(MIGRATIONS)


def test_legacy_unversioned_database_upgrades(tmp_path):
    path = tmp_path / "phil.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(db.SCHEMA)
    legacy.close()
    conn = connect(path)
    assert user_version(conn) == len(MIGRATIONS)


def test_new_migration_applies_once(tmp_path, monkeypatch):
    path = tmp_path / "phil.db"
    connect(path)
    monkeypatch.setattr(db, "MIGRATIONS", [*MIGRATIONS, "ALTER TABLE runs ADD COLUMN note TEXT"])
    conn = connect(path)
    connect(path)
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(runs)")]
    assert columns.count("note") == 1
    assert user_version(conn) == len(MIGRATIONS) + 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/store/test_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'MIGRATIONS'`.

- [ ] **Step 3: Implement** — in `src/phil/store/db.py`, keep `SCHEMA` exactly as it is, add `MIGRATIONS` and `_migrate`, and change `connect()`:

```python
MIGRATIONS: list[str] = [SCHEMA]


def _statements(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _migrate(conn: sqlite3.Connection) -> None:
    while True:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version >= len(MIGRATIONS):
                conn.execute("COMMIT")
                return
            for statement in _statements(MIGRATIONS[version]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn
```

The version is re-read inside `BEGIN IMMEDIATE`, so two processes connecting at once cannot apply the same migration twice.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/store -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/store/db.py tests/store/test_db.py
git commit -m "$(printf 'Version the database schema with ordered migrations\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 3: Strip secret-looking environment variables from child processes

**Files:**
- Modify: `src/phil/config.py` (`ShellConfig`), `src/phil/workspace/shell.py`, `docs/superpowers/specs/2026-09-23-phil-v1-design.md` (§9 child-process paragraph)
- Test: `tests/workspace/test_shell.py` (append), `tests/test_config.py` (append)

**Interfaces:**
- Consumes: `run_command(command, cwd, timeout_s)` from plan 1.
- Produces: `ShellConfig.pass_env: list[str] = []`; `phil.workspace.shell.is_secret_name(name: str) -> bool`; `child_env(environ: Mapping[str, str], pass_env: Iterable[str] = ()) -> dict[str, str]`; `run_command(command, cwd, timeout_s, env: Mapping[str, str] | None = None)`. When `env` is None, `run_command` uses `child_env(os.environ)`: filtering is the default, not opt-in.

Secret-name rule: split the upper-cased name on any non-alphanumeric character into tokens. A name is secret if any token equals one of `AUTH`, `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, `CREDENTIALS`, or ends with `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, or `PASSWD`. This catches `OPENROUTER_API_KEY`, `GITHUB_TOKEN`, `MY_APIKEY`, `NPM_AUTH` but keeps `GIT_AUTHOR_NAME` and `KEYBOARD_LAYOUT`.

- [ ] **Step 1: Write the failing tests** — append to `tests/workspace/test_shell.py`:

```python
from phil.workspace.shell import child_env, is_secret_name


@pytest.mark.parametrize(
    "name",
    ["OPENROUTER_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "MY_APIKEY", "NPM_AUTH", "gh_token"],
)
def test_secret_names_are_detected(name):
    assert is_secret_name(name)


@pytest.mark.parametrize("name", ["PATH", "HOME", "GIT_AUTHOR_NAME", "KEYBOARD_LAYOUT", "LANG", "VIRTUAL_ENV"])
def test_ordinary_names_are_kept(name):
    assert not is_secret_name(name)


def test_child_env_strips_secrets_but_honours_pass_env():
    environ = {"PATH": "/bin", "OPENROUTER_API_KEY": "sk-1", "DATABASE_TOKEN": "t"}
    assert child_env(environ) == {"PATH": "/bin"}
    assert child_env(environ, pass_env=["DATABASE_TOKEN"]) == {"PATH": "/bin", "DATABASE_TOKEN": "t"}


def test_run_command_hides_secrets_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sk-should-not-leak")
    script = tmp_path / "show_env.py"
    script.write_text("import os\nprint(os.environ.get('FAKE_API_KEY', 'absent'))\n")
    result = run_command(f"{PY} {shlex.quote(str(script))}", cwd=tmp_path, timeout_s=10)
    assert result.stdout.strip() == "absent"


def test_run_command_uses_explicit_env(tmp_path):
    script = tmp_path / "show_env.py"
    script.write_text("import os\nprint(os.environ.get('ONLY_THIS', 'absent'))\n")
    result = run_command(f"{PY} {shlex.quote(str(script))}", cwd=tmp_path, timeout_s=10, env={"ONLY_THIS": "yes"})
    assert result.stdout.strip() == "yes"
```

Append to `tests/test_config.py`:

```python
def test_pass_env_defaults_empty_and_loads(tmp_path):
    assert load_config(tmp_path).shell.pass_env == []
    (tmp_path / "phil.toml").write_text('[shell]\npass_env = ["DATABASE_TOKEN"]\n')
    assert load_config(tmp_path).shell.pass_env == ["DATABASE_TOKEN"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/workspace/test_shell.py tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'child_env'` and a `ConfigError`/`ValidationError` for `pass_env`.

- [ ] **Step 3: Implement**

In `src/phil/config.py`, add a field to `ShellConfig` after `max_output_lines`:

```python
    pass_env: list[str] = []
```

In `src/phil/workspace/shell.py`, add `import re` and `from collections.abc import Iterable, Mapping` to the imports, then add below `_RISKY_EXACT`:

```python
_SECRET_TOKENS = {"AUTH", "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "CREDENTIALS"}
_SECRET_SUFFIXES = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD")


def is_secret_name(name: str) -> bool:
    tokens = [token for token in re.split(r"[^A-Z0-9]+", name.upper()) if token]
    return any(token in _SECRET_TOKENS or token.endswith(_SECRET_SUFFIXES) for token in tokens)


def child_env(environ: Mapping[str, str], pass_env: Iterable[str] = ()) -> dict[str, str]:
    allowed = set(pass_env)
    return {name: value for name, value in environ.items() if name in allowed or not is_secret_name(name)}
```

Change the `run_command` signature and the `Popen` call:

```python
def run_command(
    command: str, cwd: Path, timeout_s: float, env: Mapping[str, str] | None = None
) -> ShellResult:
```

and inside, pass `env=dict(env) if env is not None else child_env(os.environ),` to `subprocess.Popen(...)` (add it after `start_new_session=True,`).

In the spec, replace the sentence that begins `**Child-process environment:** commands inherit Phil's environment minus secret-looking variables: any name containing` with:

```markdown
**Child-process environment:** commands inherit Phil's environment minus secret-looking variables. A name is secret when any of its `_`-separated tokens (case-insensitive) is `AUTH`, `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, or `CREDENTIAL(S)`, or ends with `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, or `PASSWD` (so `OPENROUTER_API_KEY` and `MY_APIKEY` are removed, `GIT_AUTHOR_NAME` is kept). Names listed in `[shell] pass_env` are passed through even if they match. This keeps provider API keys out of reach of agent-written test code.
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/workspace tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/config.py src/phil/workspace/shell.py tests/workspace/test_shell.py tests/test_config.py docs/superpowers/specs/2026-09-23-phil-v1-design.md
git commit -m "$(printf 'Strip secret-looking environment variables from child processes\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 4: Agent-facing contracts: field descriptions and input contracts

**Files:**
- Modify: `src/phil/contracts/common.py`, `src/phil/contracts/planning.py`, `src/phil/contracts/results.py`, `src/phil/contracts/__init__.py`
- Create: `src/phil/contracts/inputs.py`
- Test: `tests/test_contract_descriptions.py` (create)

**Interfaces:**
- Consumes: existing contracts.
- Produces: every field of `Plan`, `Task`, `PlanCritique`, `TaskResult`, `TesterReport`, `Review`, `Claim`, `SelfCheck`, `Issue` has a `description` (reaches the model through `response_format`). New input contracts importable from `phil.contracts`:
  - `ArchitectInput(goal: Goal, repo_overview: str = "", previous_plan: Plan | None = None, critique: PlanCritique | None = None)`
  - `CriticInput(goal: Goal, plan: Plan)`
  - `ImplementInput(task: Task, phase: Literal["red", "green"], test_cmd: str, last_report: TestReport | None = None)`
  - `TesterInput(plan: Plan, diff: str, final_report: TestReport, test_cmd: str)`
  - `ReviewInput(plan: Plan, diff: str, final_report: TestReport, open_assumptions: list[str] = [])`
  - `ALL_CONTRACTS` gains the five input contracts (appended in that order).

- [ ] **Step 1: Write the failing tests** — `tests/test_contract_descriptions.py`:

```python
import pytest

from phil.contracts import (
    ALL_CONTRACTS,
    ArchitectInput,
    CriticInput,
    Goal,
    ImplementInput,
    Plan,
    PlanCritique,
    ReviewInput,
    Review,
    Task,
    TaskResult,
    TesterInput,
    TesterReport,
    TestReport,
)

AGENT_OUTPUTS = [Plan, PlanCritique, TaskResult, TesterReport, Review]


def missing_descriptions(model) -> list[str]:
    schema = model.model_json_schema()
    nodes = [(model.__name__, schema), *schema.get("$defs", {}).items()]
    missing = []
    for name, node in nodes:
        for prop, prop_schema in node.get("properties", {}).items():
            if prop == "schema_version":
                continue
            if "description" not in prop_schema:
                missing.append(f"{name}.{prop}")
    return missing


@pytest.mark.parametrize("model", AGENT_OUTPUTS, ids=lambda m: m.__name__)
def test_agent_outputs_describe_every_field(model):
    assert missing_descriptions(model) == []


def make_plan() -> Plan:
    task = Task(id="MAPS-001", description="d", acceptance_criteria=["c"])
    return Plan(keyword="MAPS", description="d", tasks=[task])


def make_report() -> TestReport:
    return TestReport(command="pytest", passed=True, failures=[], log_path="logs/x.log")


def test_input_contracts_construct():
    goal = Goal(objective="Add a map section")
    plan = make_plan()
    assert ArchitectInput(goal=goal).previous_plan is None
    assert CriticInput(goal=goal, plan=plan).plan == plan
    implement = ImplementInput(task=plan.tasks[0], phase="red", test_cmd="pytest")
    assert implement.last_report is None
    assert TesterInput(plan=plan, diff="", final_report=make_report(), test_cmd="pytest").diff == ""
    assert ReviewInput(plan=plan, diff="", final_report=make_report()).open_assumptions == []


def test_input_contracts_are_exported_for_schemas():
    for model in (ArchitectInput, CriticInput, ImplementInput, TesterInput, ReviewInput):
        assert model in ALL_CONTRACTS
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_contract_descriptions.py -v`
Expected: FAIL with `ImportError: cannot import name 'ArchitectInput'`.

- [ ] **Step 3: Implement**

Replace `src/phil/contracts/common.py` with:

```python
from typing import Literal

from pydantic import Field

from phil.contracts.base import Part


class Claim(Part):
    statement: str = Field(description="What you claim is true.")
    command: str | None = Field(
        default=None, description="Exact command you ran to verify the claim. Never list a command you did not run."
    )
    observed_output: str | None = Field(default=None, description="Relevant output you observed, trimmed.")


class SelfCheck(Part):
    assumptions: list[str] = Field(description="Things you took as given without verifying.")
    evidence: list[Claim] = Field(description="Claims backed by a command you ran and what it showed.")
    risks: list[str] = Field(description="Ways this output could be wrong.")
    unverified: list[str] = Field(description="Things you could not check.")
    out_of_scope: list[str] = Field(
        description="Problems you noticed that are not your job now. Record them here instead of acting on them."
    )


class Issue(Part):
    task_id: str | None = Field(default=None, description="Task id the issue belongs to, if any.")
    file: str | None = Field(default=None, description="Repo-relative file path, if any.")
    line: int | None = Field(default=None, description="1-based line number, if any.")
    severity: Literal["blocker", "major", "minor"] = Field(
        description="blocker: must fix before merge; major: should fix; minor: optional."
    )
    note: str = Field(description="What is wrong and why it matters, in one or two sentences.")
```

In `src/phil/contracts/planning.py`, replace the `Task`, `Plan` field lines and `PlanCritique` fields (keep the validator and patterns unchanged):

```python
class Task(Part):
    id: str = Field(pattern=TASK_ID_PATTERN, description="KEYWORD-### id, e.g. MAPS-001.")
    description: str = Field(description="One atomic change a developer can test-drive in isolation.")
    acceptance_criteria: list[str] = Field(
        min_length=1, description="Observable behaviours a failing test can check. At least one."
    )
    files_hint: list[str] = Field(default=[], description="Repo-relative files this task most likely touches.")
    status: Literal["TODO", "DONE", "SKIPPED", "FAILED"] = Field(
        default="TODO", description="Always TODO in a new plan."
    )


class Plan(Contract):
    keyword: str = Field(pattern=KEYWORD_PATTERN, description="3-6 uppercase letters naming the objective.")
    description: str = Field(description="One or two sentences on the approach.")
    tasks: list[Task] = Field(min_length=1, description="Ordered atomic tasks; each leaves the app green.")
    test_cmd: str | None = Field(default=None, description="Command that runs the project's tests, e.g. 'uv run pytest'.")
    story_ref: str | None = Field(default=None, description="Roadmap story reference, if given in the goal.")
    critic_notes: list[str] = Field(default=[], description="Leave empty; filled from the plan critique.")
```

```python
class PlanCritique(Contract):
    verdict: Literal["ok", "revise"] = Field(description="revise only for problems worth another planning round.")
    issues: list[Issue] = Field(description="Concrete problems, each tied to a task_id where possible.")
    notes: list[str] = Field(description="Short notes for the user about the plan's risks.")
    self_check: SelfCheck = Field(description="Your self-check of this critique.")
```

In `src/phil/contracts/results.py`, replace `TaskResult`, `TesterReport`, `Review` (leave `TestReport` unchanged; it is produced by code):

```python
class TaskResult(Contract):
    phase: Literal["red", "green"] = Field(description="The phase you were asked to do.")
    summary: str = Field(max_length=600, description="What you changed and why, in a few terse lines.")
    files_changed: list[str] = Field(description="Repo-relative paths you created, edited, or deleted.")
    tests_added: list[str] = Field(description="Repo-relative test files you created or extended.")
    self_check: SelfCheck = Field(description="Your self-check of this work.")


class TesterReport(Contract):
    tests_added: list[str] = Field(description="Repo-relative test files you added (integration, E2E, edge cases).")
    issues: list[Issue] = Field(description="Defects found, including weak or misleading unit tests.")
    self_check: SelfCheck = Field(description="Your self-check of this testing pass.")


class Review(Contract):
    verdict: Literal["approve", "changes"] = Field(description="approve only if no blocker or major issue remains.")
    issues: list[Issue] = Field(description="Problems in the diff, most severe first.")
    assumption_resolutions: list[str] = Field(
        description="For each open assumption: 'confirmed: ...' or 'issue raised: ...'."
    )
    self_check: SelfCheck = Field(description="Your self-check of this review.")
```

Create `src/phil/contracts/inputs.py`:

```python
from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract
from phil.contracts.interface import Goal
from phil.contracts.planning import Plan, PlanCritique, Task
from phil.contracts.results import TestReport


class ArchitectInput(Contract):
    goal: Goal
    repo_overview: str = ""
    previous_plan: Plan | None = None
    critique: PlanCritique | None = None


class CriticInput(Contract):
    goal: Goal
    plan: Plan


class ImplementInput(Contract):
    task: Task
    phase: Literal["red", "green"]
    test_cmd: str
    last_report: TestReport | None = None


class TesterInput(Contract):
    plan: Plan
    diff: str
    final_report: TestReport
    test_cmd: str


class ReviewInput(Contract):
    plan: Plan
    diff: str
    final_report: TestReport
    open_assumptions: list[str] = Field(default_factory=list)
```

In `src/phil/contracts/__init__.py`, add the import line

```python
from phil.contracts.inputs import ArchitectInput, CriticInput, ImplementInput, ReviewInput, TesterInput
```

append `ArchitectInput, CriticInput, ImplementInput, TesterInput, ReviewInput` (in that order) to the end of `ALL_CONTRACTS`, and add the five names to `__all__` in alphabetical position.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_contract_descriptions.py tests/test_contracts.py tests/test_cli.py -v`
Expected: all PASS (the schema-export tests count `ALL_CONTRACTS`, so they adapt automatically).

- [ ] **Step 5: Commit**

```bash
git add src/phil/contracts tests/test_contract_descriptions.py
git commit -m "$(printf 'Describe agent-facing contract fields and add input contracts\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 5: Context packets with token budgets

**Files:**
- Create: `src/phil/packets/__init__.py`, `src/phil/packets/packet.py`
- Test: `tests/packets/__init__.py` (empty), `tests/packets/test_packet.py`

**Interfaces:**
- Consumes: `phil.contracts.Contract`.
- Produces (importable from `phil.packets`): `estimate_tokens(text: str) -> int` (`(len(text) + 3) // 4`); `class PacketTooLarge(Exception)`; pydantic `Packet(role: str, contract_type: str, contract_json: str, files: dict[str, str], ledger: list[str], omitted: list[str], tokens: int)` with `render() -> str`; `build_packet(role: str, contract: Contract, *, budget_tokens: int, root: Path | None = None, files: Sequence[str] = (), ledger: Sequence[str] = ()) -> Packet`.

Trimming is deterministic and follows spec §9's priority: the contract always goes in (raise `PacketTooLarge` if it alone exceeds the budget), then files in the given order (a file that does not fit is skipped whole and listed in `omitted`; later smaller files may still fit), then ledger entries in order until the budget is reached (the rest are summarized in `omitted` as `"<n> ledger entries"`). Missing files are listed in `omitted` as `"<path> (not found)"`. File paths must be relative and stay under `root` (`ValueError` otherwise).

- [ ] **Step 1: Write the failing tests** — `tests/packets/test_packet.py`:

```python
import pytest

from phil.contracts import Goal
from phil.packets import PacketTooLarge, build_packet, estimate_tokens


def goal() -> Goal:
    return Goal(objective="Add a subtract function")


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_contract_only_packet_renders_contract():
    packet = build_packet("critic", goal(), budget_tokens=1000)
    text = packet.render()
    assert "## Input (Goal)" in text
    assert "Add a subtract function" in text
    assert packet.tokens == estimate_tokens(text)
    assert packet.omitted == []


def test_contract_over_budget_raises():
    with pytest.raises(PacketTooLarge):
        build_packet("critic", goal(), budget_tokens=5)


def test_files_added_in_order_until_budget(tmp_path):
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "big.py").write_text("y = 2\n" * 2000)
    (tmp_path / "tiny.py").write_text("z = 3\n")
    packet = build_packet(
        "implementer", goal(), budget_tokens=300, root=tmp_path, files=["small.py", "big.py", "tiny.py"]
    )
    assert list(packet.files) == ["small.py", "tiny.py"]
    assert packet.omitted == ["big.py"]
    assert packet.tokens <= 300


def test_missing_file_is_reported(tmp_path):
    packet = build_packet("implementer", goal(), budget_tokens=1000, root=tmp_path, files=["nope.py"])
    assert packet.omitted == ["nope.py (not found)"]


def test_ledger_trimmed_after_files(tmp_path):
    ledger = [f"assumption number {i} about the listing model" for i in range(200)]
    packet = build_packet("reviewer", goal(), budget_tokens=400, ledger=ledger)
    assert 0 < len(packet.ledger) < 200
    assert packet.ledger == ledger[: len(packet.ledger)]
    assert packet.omitted == [f"{200 - len(packet.ledger)} ledger entries"]
    assert packet.tokens <= 400


def test_paths_must_stay_under_root(tmp_path):
    with pytest.raises(ValueError):
        build_packet("implementer", goal(), budget_tokens=1000, root=tmp_path, files=["../secret.txt"])
    with pytest.raises(ValueError):
        build_packet("implementer", goal(), budget_tokens=1000, root=tmp_path, files=["/etc/passwd"])


def test_build_is_deterministic(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n")
    first = build_packet("implementer", goal(), budget_tokens=500, root=tmp_path, files=["a.py"], ledger=["x"])
    second = build_packet("implementer", goal(), budget_tokens=500, root=tmp_path, files=["a.py"], ledger=["x"])
    assert first.render() == second.render()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/packets -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.packets'`.

- [ ] **Step 3: Implement**

`src/phil/packets/packet.py`:

```python
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from phil.contracts import Contract


class PacketTooLarge(Exception):
    pass


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _contract_section(contract_type: str, contract_json: str) -> str:
    return f"## Input ({contract_type})\n```json\n{contract_json}\n```\n"


def _file_section(path: str, content: str) -> str:
    return f"### {path}\n```\n{content}\n```\n"


_FILES_HEADER = "\n## Files\n"
_LEDGER_HEADER = "\n## Assumptions so far\n"
_OMITTED_HEADER = "\n## Omitted for budget\n"


class Packet(BaseModel):
    role: str
    contract_type: str
    contract_json: str
    files: dict[str, str]
    ledger: list[str]
    omitted: list[str]
    tokens: int = 0

    def render(self) -> str:
        parts = [_contract_section(self.contract_type, self.contract_json)]
        if self.files:
            parts.append(_FILES_HEADER)
            parts.extend(_file_section(path, content) for path, content in self.files.items())
        if self.ledger:
            parts.append(_LEDGER_HEADER)
            parts.extend(f"- {entry}\n" for entry in self.ledger)
        if self.omitted:
            parts.append(_OMITTED_HEADER)
            parts.extend(f"- {item}\n" for item in self.omitted)
        return "".join(parts)


def _resolve_under(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"file path must be relative and inside the root: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"file path escapes the root: {relative!r}")
    return path


def build_packet(
    role: str,
    contract: Contract,
    *,
    budget_tokens: int,
    root: Path | None = None,
    files: Sequence[str] = (),
    ledger: Sequence[str] = (),
) -> Packet:
    contract_type = type(contract).__name__
    contract_json = contract.model_dump_json(indent=2)
    used = estimate_tokens(_contract_section(contract_type, contract_json))
    if used > budget_tokens:
        raise PacketTooLarge(f"{contract_type} alone needs {used} tokens; budget is {budget_tokens}")
    # Reserve room for the omitted section so the rendered packet stays within budget.
    reserve = estimate_tokens(_OMITTED_HEADER) + sum(estimate_tokens(f"- {path} (not found)\n") for path in files) + 10

    kept_files: dict[str, str] = {}
    omitted: list[str] = []
    header_cost = estimate_tokens(_FILES_HEADER)
    for relative in files:
        if root is None:
            raise ValueError("root is required when files are given")
        path = _resolve_under(root, relative)
        if not path.is_file():
            omitted.append(f"{relative} (not found)")
            continue
        cost = estimate_tokens(_file_section(relative, path.read_text(errors="replace")))
        extra = cost + (header_cost if not kept_files else 0)
        if used + extra + reserve <= budget_tokens:
            kept_files[relative] = path.read_text(errors="replace")
            used += extra
        else:
            omitted.append(relative)

    kept_ledger: list[str] = []
    ledger_header_cost = estimate_tokens(_LEDGER_HEADER)
    for entry in ledger:
        extra = estimate_tokens(f"- {entry}\n") + (ledger_header_cost if not kept_ledger else 0)
        if used + extra + reserve > budget_tokens:
            break
        kept_ledger.append(entry)
        used += extra
    if len(kept_ledger) < len(ledger):
        omitted.append(f"{len(ledger) - len(kept_ledger)} ledger entries")

    packet = Packet(
        role=role,
        contract_type=contract_type,
        contract_json=contract_json,
        files=kept_files,
        ledger=kept_ledger,
        omitted=omitted,
    )
    packet.tokens = estimate_tokens(packet.render())
    return packet
```

`src/phil/packets/__init__.py`:

```python
from phil.packets.packet import Packet, PacketTooLarge, build_packet, estimate_tokens

__all__ = ["Packet", "PacketTooLarge", "build_packet", "estimate_tokens"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/packets -v`
Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/packets tests/packets
git commit -m "$(printf 'Add context packets with deterministic budget trimming\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 6: Agent specs, role prompts, and registry

**Files:**
- Create: `src/phil/agents/__init__.py` (empty), `src/phil/agents/spec.py`, `src/phil/agents/registry.py`, `src/phil/prompts/__init__.py` (empty), `src/phil/prompts/_shared.md`, `src/phil/prompts/architect.md`, `src/phil/prompts/critic.md`, `src/phil/prompts/implementer.md`, `src/phil/prompts/tester.md`, `src/phil/prompts/reviewer.md`
- Test: `tests/agents/__init__.py` (empty), `tests/agents/test_registry.py`

**Interfaces:**
- Consumes: input and output contracts from Task 4; `phil.config.ROLES`.
- Produces: frozen dataclass `AgentSpec(name: str, role: str, in_contract: type[Contract], out_contract: type[Contract], tools: tuple[str, ...] = (), writes_files: bool = False)`; `load_prompt(spec: AgentSpec) -> str` (role prompt + blank line + `_shared.md`); `phil.agents.registry.SPECS: dict[str, AgentSpec]` for `architect`, `critic`, `implementer`, `tester`, `reviewer`; `get_spec(name: str) -> AgentSpec` (`KeyError` if unknown). Only `implementer` and `tester` have `tools=("shell",)` and `writes_files=True`.

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_registry.py`:

```python
import pytest

from phil.agents.registry import SPECS, get_spec
from phil.agents.spec import load_prompt
from phil.config import ROLES
from phil.contracts import Plan, PlanCritique, Review, TaskResult, TesterReport


def test_registry_covers_run_roles():
    assert set(SPECS) == {"architect", "critic", "implementer", "tester", "reviewer"}
    assert all(spec.role in ROLES for spec in SPECS.values())


@pytest.mark.parametrize(
    ("name", "out_contract"),
    [
        ("architect", Plan),
        ("critic", PlanCritique),
        ("implementer", TaskResult),
        ("tester", TesterReport),
        ("reviewer", Review),
    ],
)
def test_output_contracts(name, out_contract):
    assert get_spec(name).out_contract is out_contract


def test_only_implementer_and_tester_write_and_run_commands():
    writers = {name for name, spec in SPECS.items() if spec.writes_files}
    shell_users = {name for name, spec in SPECS.items() if "shell" in spec.tools}
    assert writers == shell_users == {"implementer", "tester"}


@pytest.mark.parametrize("name", ["architect", "critic", "implementer", "tester", "reviewer"])
def test_prompts_load_with_shared_block(name):
    prompt = load_prompt(get_spec(name))
    assert prompt.startswith("# ")
    assert "## Self-check (required)" in prompt


def test_unknown_spec_raises():
    with pytest.raises(KeyError):
        get_spec("wizard")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.agents'`.

- [ ] **Step 3: Implement the specs**

`src/phil/agents/spec.py`:

```python
from dataclasses import dataclass
from importlib.resources import files

from phil.contracts import Contract


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    in_contract: type[Contract]
    out_contract: type[Contract]
    tools: tuple[str, ...] = ()
    writes_files: bool = False


def _read_prompt(filename: str) -> str:
    return files("phil.prompts").joinpath(filename).read_text()


def load_prompt(spec: AgentSpec) -> str:
    return f"{_read_prompt(f'{spec.name}.md').rstrip()}\n\n{_read_prompt('_shared.md').rstrip()}\n"
```

`src/phil/agents/registry.py`:

```python
from phil.agents.spec import AgentSpec
from phil.contracts import (
    ArchitectInput,
    CriticInput,
    ImplementInput,
    Plan,
    PlanCritique,
    Review,
    ReviewInput,
    TaskResult,
    TesterInput,
    TesterReport,
)

SPECS: dict[str, AgentSpec] = {
    "architect": AgentSpec("architect", "architect", ArchitectInput, Plan),
    "critic": AgentSpec("critic", "critic", CriticInput, PlanCritique),
    "implementer": AgentSpec(
        "implementer", "implementer", ImplementInput, TaskResult, tools=("shell",), writes_files=True
    ),
    "tester": AgentSpec("tester", "tester", TesterInput, TesterReport, tools=("shell",), writes_files=True),
    "reviewer": AgentSpec("reviewer", "reviewer", ReviewInput, Review),
}


def get_spec(name: str) -> AgentSpec:
    return SPECS[name]
```

- [ ] **Step 4: Write the prompts**

`src/phil/prompts/_shared.md`:

```markdown
## Output discipline
- Return only the structured output requested. Fill every field.
- Write tersely: fragments are fine, no filler, never restate your input.
- Put the essential point first. One idea per list item.
- Stay inside your role. If you notice other problems, record them in `self_check.out_of_scope` instead of acting on them.

## Self-check (required)
Before returning, check your own work and fill `self_check`:
- `assumptions`: what you took as given without verifying.
- `evidence`: claims backed by a command you actually ran, with the output you saw. Never list a command you did not run.
- `risks`: how this output could be wrong.
- `unverified`: what you could not check.
- `out_of_scope`: problems you noticed that are not your job right now.
```

`src/phil/prompts/architect.md`:

```markdown
# Role: Architect

You turn a goal into an execution plan for a test-driven developer. You can read the repository; you never modify it.

## Tasks
- Break the goal into atomic tasks. A task is right-sized when a developer can write a failing test for it, make that test pass, and leave the app working, with no more than two or three logical changes.
- Order tasks so each builds on the previous and the app stays green after every task.
- Give every task observable `acceptance_criteria` that a test can check.
- Fill `files_hint` with the files you expect the task to touch, based on reading the code. Accurate hints save the developer from searching.

## Identifiers
- Choose a `keyword` of 3-6 uppercase letters naming the objective (e.g. MAPS for a map feature).
- Task ids are `<KEYWORD>-001`, `<KEYWORD>-002`, and so on. Every task starts with status `TODO`.

## Test command
- Set `test_cmd` to the command that runs this project's tests, found from its config (pyproject.toml, package.json, Makefile).

## Revisions
- If your input includes `previous_plan` and `critique`, revise the previous plan to resolve every critique issue you agree with, and keep what was right.
```

`src/phil/prompts/critic.md`:

```markdown
# Role: Plan Critic

You challenge a plan before a human sees it. You are a different reviewer from the architect who wrote it; assume it has blind spots.

## Check
- Tasks too large to test-drive in one step, or not independently verifiable.
- Missing or untestable acceptance criteria.
- `files_hint` entries that look wrong or incomplete.
- Parts of the system the goal clearly affects that no task mentions (migrations, config, docs, callers).
- Ordering that would leave the app broken between tasks.
- A missing or wrong `test_cmd`.

## Verdict
- `revise` only when an issue would cause real rework if left. Otherwise `ok`, with your concerns in `notes`.
- Tie each issue to a `task_id` where possible. You have no shell; do not claim to have run commands.
```

`src/phil/prompts/implementer.md`:

```markdown
# Role: Implementer

You implement one task test-first inside an isolated git worktree. Your input names the task, its acceptance criteria, the phase, and the test command. Use the `run_shell` tool to run allowlisted commands.

## Phase: red
- Write failing tests that check the acceptance criteria. Change only test files.
- Run the test command and confirm the new tests fail for the expected reason (a missing function or wrong behaviour, not a typo).

## Phase: green
- Make the failing tests pass with the simplest correct change. Do not edit, weaken, or delete tests.
- Run the test command and confirm everything passes.
- If your input includes `last_report`, fix the failures it lists before anything else.

## Rules
- Follow the repository's existing style and conventions.
- Touch only what the task needs. Report other problems in `self_check.out_of_scope`.
- List every file you changed in `files_changed` and every test file you added or extended in `tests_added`.
```

`src/phil/prompts/tester.md`:

```markdown
# Role: Tester

You test a finished change with more rigour than the developer did. The developer's unit tests already pass; your job is to find what they missed.

## Do
- Add integration, end-to-end, and edge-case tests that exercise the change as a whole against the plan's acceptance criteria.
- Audit the developer's tests: flag tests that assert nothing meaningful, mock away the behaviour under test, or miss obvious cases.
- Run the test command with `run_shell` and report what fails.

## Do not
- Do not fix product code. Report defects as issues with a severity; fixes are scheduled from your report.
```

`src/phil/prompts/reviewer.md`:

```markdown
# Role: Reviewer

You review the complete diff of a run before it is handed to a human. You cannot run commands; judge from the plan, the diff, and the final test report.

## Check
- Does the diff do what the plan says, no more and no less?
- Correctness, error handling, edge cases, security, and fit with the repository's conventions.
- Tests that check real behaviour rather than mocks.

## Assumptions
- Your input lists open assumptions recorded by earlier agents. For each one, add an entry to `assumption_resolutions`: `confirmed: <why>` if the diff or tests support it, or `issue raised: <summary>` with a matching issue if it does not.

## Verdict
- `approve` only when no blocker or major issue remains. Order issues by severity.
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/agents/test_registry.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/phil/agents src/phil/prompts tests/agents
git commit -m "$(printf 'Add agent specs, role prompts, and registry\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 7: Shell tool for agents

**Files:**
- Create: `src/phil/agents/tools.py`
- Test: `tests/agents/test_tools.py`

**Interfaces:**
- Consumes: `ShellConfig` (with `pass_env`), `ShellPolicy`, `run_command(..., env=)`, `child_env`, `truncate_output` (Task 3 / plan 1); `ArtifactStore.write_log`.
- Produces: dataclass `CommandLog(commands: list[str])`; `make_shell_tool(workdir: Path, shell: ShellConfig, log: CommandLog, artifacts: ArtifactStore | None = None) -> Callable[[str], str]`. The returned function is named `run_shell`, has a docstring and type hints (deepagents turns plain callables into tools), records every allowed command in `log.commands`, returns `DENIED: ...` without running for disallowed commands (plan 3 replaces this with a human-approval interrupt), and returns `exit_code: <n>` plus truncated output. When `artifacts` is given, the full output is saved with `write_log(f"shell-{n}", ...)` and the log path is included in the result.

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_tools.py`:

```python
import shlex
import sys

from phil.agents.tools import CommandLog, make_shell_tool
from phil.config import ShellConfig
from phil.store.artifacts import ArtifactStore

PY = shlex.quote(sys.executable)


def shell_config(**overrides) -> ShellConfig:
    return ShellConfig(**({"allow": [f"{sys.executable} *"], "timeout_s": 10} | overrides))


def test_allowed_command_runs_and_is_logged(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    log = CommandLog()
    run_shell = make_shell_tool(tmp_path, shell_config(), log)
    output = run_shell(f"{PY} hello.py")
    assert output.startswith("exit_code: 0")
    assert "hi" in output
    assert log.commands == [f"{PY} hello.py"]


def test_denied_command_is_not_run_or_logged(tmp_path):
    log = CommandLog()
    run_shell = make_shell_tool(tmp_path, shell_config(), log)
    output = run_shell("rm -rf /")
    assert output.startswith("DENIED:")
    assert log.commands == []


def test_long_output_is_truncated_and_saved(tmp_path):
    (tmp_path / "spam.py").write_text("for i in range(1000):\n    print(i)\n")
    artifacts = ArtifactStore(tmp_path / "run")
    run_shell = make_shell_tool(tmp_path, shell_config(max_output_lines=20), CommandLog(), artifacts)
    output = run_shell(f"{PY} spam.py")
    assert "lines omitted" in output
    assert "full log: " in output
    assert (tmp_path / "run" / "logs" / "shell-1.log").read_text().count("\n") >= 1000


def test_secrets_are_not_visible_to_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sk-should-not-leak")
    (tmp_path / "env.py").write_text("import os\nprint(os.environ.get('FAKE_API_KEY', 'absent'))\n")
    output = make_shell_tool(tmp_path, shell_config(), CommandLog())(f"{PY} env.py")
    assert "absent" in output
    assert "sk-should-not-leak" not in output


def test_pass_env_lets_named_variables_through(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "needed")
    (tmp_path / "env.py").write_text("import os\nprint(os.environ.get('SERVICE_TOKEN', 'absent'))\n")
    output = make_shell_tool(tmp_path, shell_config(pass_env=["SERVICE_TOKEN"]), CommandLog())(f"{PY} env.py")
    assert "needed" in output


def test_tool_is_named_and_documented(tmp_path):
    run_shell = make_shell_tool(tmp_path, shell_config(), CommandLog())
    assert run_shell.__name__ == "run_shell"
    assert run_shell.__doc__
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.agents.tools'`.

- [ ] **Step 3: Implement** — `src/phil/agents/tools.py`:

```python
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from phil.config import ShellConfig
from phil.store.artifacts import ArtifactStore
from phil.workspace.shell import ShellPolicy, child_env, run_command, truncate_output


@dataclass
class CommandLog:
    commands: list[str] = field(default_factory=list)


def make_shell_tool(
    workdir: Path,
    shell: ShellConfig,
    log: CommandLog,
    artifacts: ArtifactStore | None = None,
) -> Callable[[str], str]:
    policy = ShellPolicy(shell.allow)
    env = child_env(os.environ, shell.pass_env)

    def run_shell(command: str) -> str:
        """Run one allowlisted command in the task worktree.

        Returns the exit code and the command's output (long output is trimmed).
        Only commands matching the project's allowlist run; others are denied.
        Shell operators such as pipes, redirects, `;` and `&&` are not allowed.
        """
        if not policy.is_allowed(command):
            allowed = ", ".join(shell.allow)
            return f"DENIED: `{command}` is not on the allowlist. Allowed patterns: {allowed}"
        log.commands.append(command)
        result = run_command(command, workdir, shell.timeout_s, env=env)
        full_output = result.stdout + (f"\n[stderr]\n{result.stderr}" if result.stderr else "")
        status = f"exit_code: {result.exit_code}" + (" (timed out)" if result.timed_out else "")
        lines = [status]
        if artifacts is not None:
            path = artifacts.write_log(f"shell-{len(log.commands)}", full_output)
            lines.append(f"full log: {path}")
        lines.append(truncate_output(full_output, shell.max_output_lines))
        return "\n".join(lines)

    return run_shell
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agents/test_tools.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/tools.py tests/agents/test_tools.py
git commit -m "$(printf 'Add policy-checked shell tool for agents\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 8: Usage extraction and `FakeAgent`

**Files:**
- Create: `src/phil/agents/usage.py`, `src/phil/agents/fake.py`
- Test: `tests/agents/test_fake_usage.py`

**Interfaces:**
- Consumes: `AgentSpec`.
- Produces:
  - `phil.agents.usage.Usage(input_tokens: int, output_tokens: int, cost_usd: float)` frozen dataclass; `extract_usage(messages: Iterable[object]) -> Usage` sums `usage_metadata["input_tokens"/"output_tokens"]` and `response_metadata["cost"]` across messages, treating missing attributes or keys as 0 (works with LangChain `AIMessage` and `FakeMessage`).
  - `phil.agents.fake.FakeMessage(usage_metadata: dict | None = None, response_metadata: dict = {}, content: str = "")`.
  - `FakeAgent(outputs: list[object], usage: tuple[int, int, float] = (100, 20, 0.0))` with `invoke(payload: dict) -> dict` returning `{"messages": [FakeMessage(...)], "structured_response": <next output>}`; if the next output is an exception it is raised; `calls: list[dict]` records every payload.
  - `FakeAgentFactory(outputs, usage=(100, 20, 0.0))`, callable as `factory(spec, model, workdir, tools) -> FakeAgent`; records `built: list[tuple[str, str]]` (spec name, model) and `tools_seen: list[list[str]]` (tool `__name__`s).

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_fake_usage.py`:

```python
import pytest

from phil.agents.fake import FakeAgentFactory, FakeMessage
from phil.agents.registry import get_spec
from phil.agents.usage import Usage, extract_usage


def test_extract_usage_sums_messages():
    messages = [
        FakeMessage(usage_metadata={"input_tokens": 100, "output_tokens": 10}, response_metadata={"cost": 0.01}),
        object(),
        FakeMessage(usage_metadata={"input_tokens": 50, "output_tokens": 5}),
    ]
    assert extract_usage(messages) == Usage(150, 15, 0.01)


def test_extract_usage_of_nothing_is_zero():
    assert extract_usage([]) == Usage(0, 0, 0.0)


def test_fake_factory_returns_outputs_in_order():
    factory = FakeAgentFactory(["first", "second"], usage=(10, 2, 0.5))
    agent = factory(get_spec("critic"), "model-x", None, [])
    first = agent.invoke({"messages": [{"role": "user", "content": "a"}]})
    second = agent.invoke({"messages": []})
    assert (first["structured_response"], second["structured_response"]) == ("first", "second")
    assert extract_usage(first["messages"]) == Usage(10, 2, 0.5)
    assert factory.built == [("critic", "model-x")]
    assert agent.calls[0]["messages"][0]["content"] == "a"


def test_fake_agent_raises_queued_exceptions():
    agent = FakeAgentFactory([RuntimeError("boom")])(get_spec("critic"), "m", None, [])
    with pytest.raises(RuntimeError, match="boom"):
        agent.invoke({"messages": []})


def test_fake_factory_records_tool_names():
    def run_shell(command: str) -> str:
        return command

    factory = FakeAgentFactory([])
    factory(get_spec("implementer"), "m", None, [run_shell])
    assert factory.tools_seen == [["run_shell"]]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_fake_usage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.agents.fake'`.

- [ ] **Step 3: Implement**

`src/phil/agents/usage.py`:

```python
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float


def extract_usage(messages: Iterable[object]) -> Usage:
    input_tokens = output_tokens = 0
    cost = 0.0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        input_tokens += int(usage.get("input_tokens", 0) or 0)
        output_tokens += int(usage.get("output_tokens", 0) or 0)
        metadata = getattr(message, "response_metadata", None) or {}
        cost += float(metadata.get("cost", 0.0) or 0.0)
    return Usage(input_tokens, output_tokens, cost)
```

`src/phil/agents/fake.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from phil.agents.spec import AgentSpec


@dataclass
class FakeMessage:
    usage_metadata: dict | None = None
    response_metadata: dict = field(default_factory=dict)
    content: str = ""


class FakeAgent:
    def __init__(self, outputs: list[object], usage: tuple[int, int, float] = (100, 20, 0.0)) -> None:
        self.outputs = list(outputs)
        self.usage = usage
        self.calls: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.calls.append(payload)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        input_tokens, output_tokens, cost = self.usage
        message = FakeMessage(
            usage_metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            response_metadata={"cost": cost},
        )
        return {"messages": [message], "structured_response": output}


class FakeAgentFactory:
    def __init__(self, outputs: list[object], usage: tuple[int, int, float] = (100, 20, 0.0)) -> None:
        self.agent = FakeAgent(outputs, usage)
        self.built: list[tuple[str, str]] = []
        self.tools_seen: list[list[str]] = []

    def __call__(
        self, spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]
    ) -> FakeAgent:
        self.built.append((spec.name, model))
        self.tools_seen.append([tool.__name__ for tool in tools])
        return self.agent
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agents/test_fake_usage.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/usage.py src/phil/agents/fake.py tests/agents/test_fake_usage.py
git commit -m "$(printf 'Add usage extraction and FakeAgent for tests\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 9: `invoke_agent`: contract validation, retry, telemetry, artifacts

**Files:**
- Create: `src/phil/agents/invoke.py`
- Test: `tests/agents/conftest.py`, `tests/agents/test_invoke.py`

**Interfaces:**
- Consumes: `AgentSpec`, `get_spec` (Task 6); `Packet`, `build_packet` (Task 5); `CommandLog`, `make_shell_tool` (Task 7); `extract_usage`, `FakeAgentFactory` (Task 8); `PhilConfig`; `TelemetryRow`, `record` (plan 1); `ArtifactStore`, `artifact_name` (plan 1).
- Produces:
  - `AgentFactory` type alias: `Callable[[AgentSpec, str, Path | None, list[Callable[..., str]]], Any]` (the returned agent has `invoke(dict) -> dict`).
  - dataclass `AgentContext(config: PhilConfig, conn: sqlite3.Connection, layer: Literal["chat", "run"], run_id: str | None = None, artifacts: ArtifactStore | None = None, workdir: Path | None = None, factory: AgentFactory | None = None, sleep: Callable[[float], None] = time.sleep)`. When `factory` is None, `invoke_agent` uses `phil.agents.factory.build_deep_agent` (Task 12), imported inside the function.
  - `class ContractViolation(Exception)` with `.agent: str` and `.problems: list[str]`.
  - `invoke_agent(spec: AgentSpec, packet: Packet, ctx: AgentContext, *, node: str, task_id: str | None = None) -> Contract`.

Behaviour: builds one agent (shell tool included only when `"shell" in spec.tools` and `ctx.workdir` is set), makes at most two attempts. Each attempt saves the packet (`packets/<artifact_name(node, task_id, attempt)>`), invokes the agent with the rendered packet as the user message (attempt 2 adds a second user message listing the problems), records one telemetry row (`packet_tokens=packet.tokens`, usage from `extract_usage`, `outcome` `ok` or `invalid`), and validates `structured_response` against `spec.out_contract` (accepts an instance, a dict, or another pydantic model via `model_dump()`; `None` is invalid). On success it saves the output (`outputs/<name>`) and returns it. After two invalid attempts it raises `ContractViolation`. Evidence checks, ledger/parking side effects, and provider retries arrive in Tasks 10 and 11; leave the marked hook points.

- [ ] **Step 1: Write the failing tests**

`tests/agents/conftest.py`:

```python
import pytest

from phil.config import PhilConfig
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
    return PhilConfig()
```

`tests/agents/test_invoke.py`:

```python
import pytest

from phil.agents.fake import FakeAgentFactory
from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.registry import get_spec
from phil.config import DEFAULT_MODEL
from phil.contracts import PlanCritique
from tests.agents.conftest import critique


def telemetry(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM telemetry ORDER BY id")]


def context(config, conn, artifacts, factory, **overrides):
    values = dict(config=config, conn=conn, layer="run", run_id="r-0001", artifacts=artifacts, factory=factory)
    return AgentContext(**(values | overrides))


def test_valid_output_is_returned_recorded_and_saved(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique()], usage=(120, 30, 0.002))
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert isinstance(result, PlanCritique)
    assert factory.built == [("critic", DEFAULT_MODEL)]
    [row] = telemetry(conn)
    assert (row["role"], row["outcome"], row["attempt"]) == ("critic", "ok", 1)
    assert (row["input_tokens"], row["output_tokens"], row["packet_tokens"]) == (120, 30, critic_packet.tokens)
    assert (artifacts.run_dir / "packets" / "critic-run-1.json").exists()
    assert (artifacts.run_dir / "outputs" / "critic-run-1.json").exists()


def test_packet_is_sent_as_user_message(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    [message] = factory.agent.calls[0]["messages"]
    assert message == {"role": "user", "content": critic_packet.render()}


def test_dict_output_is_validated(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([critique().model_dump()])
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert result == critique()


def test_invalid_then_valid_retries_with_problems(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([{"verdict": "maybe"}, critique()])
    result = invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert isinstance(result, PlanCritique)
    assert [row["outcome"] for row in telemetry(conn)] == ["invalid", "ok"]
    retry_messages = factory.agent.calls[1]["messages"]
    assert len(retry_messages) == 2
    assert "verdict" in retry_messages[1]["content"]


def test_two_invalid_attempts_raise(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([None, {"verdict": "maybe"}])
    with pytest.raises(ContractViolation) as excinfo:
        invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory), node="critic")
    assert excinfo.value.agent == "critic"
    assert excinfo.value.problems
    assert [row["outcome"] for row in telemetry(conn)] == ["invalid", "invalid"]


def test_works_without_artifacts_or_run(config, conn, critic_packet):
    factory = FakeAgentFactory([critique()])
    ctx = context(config, conn, None, factory, layer="chat", run_id=None)
    assert isinstance(invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic"), PlanCritique)
    assert telemetry(conn)[0]["run_id"] is None


def test_shell_tool_given_only_to_shell_roles_with_workdir(config, conn, artifacts, critic_packet, tmp_path):
    factory = FakeAgentFactory([critique(), critique()])
    invoke_agent(get_spec("critic"), critic_packet, context(config, conn, artifacts, factory, workdir=tmp_path), node="critic")
    assert factory.tools_seen == [[]]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_invoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.agents.invoke'`.

- [ ] **Step 3: Implement** — `src/phil/agents/invoke.py`:

```python
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from phil.agents.spec import AgentSpec
from phil.agents.tools import CommandLog, make_shell_tool
from phil.agents.usage import extract_usage
from phil.config import PhilConfig
from phil.contracts import Contract
from phil.packets import Packet
from phil.store.artifacts import ArtifactStore, artifact_name
from phil.store.telemetry import TelemetryRow, record

AgentFactory = Callable[[AgentSpec, str, Path | None, list[Callable[..., str]]], Any]


@dataclass
class AgentContext:
    config: PhilConfig
    conn: sqlite3.Connection
    layer: Literal["chat", "run"]
    run_id: str | None = None
    artifacts: ArtifactStore | None = None
    workdir: Path | None = None
    factory: AgentFactory | None = None
    sleep: Callable[[float], None] = time.sleep


class ContractViolation(Exception):
    def __init__(self, agent: str, problems: list[str]) -> None:
        super().__init__(agent, problems)
        self.agent = agent
        self.problems = problems

    def __str__(self) -> str:
        return f"{self.agent} returned invalid output: {'; '.join(self.problems)}"


def _resolve_factory(ctx: AgentContext) -> AgentFactory:
    if ctx.factory is not None:
        return ctx.factory
    from phil.agents.factory import build_deep_agent

    return build_deep_agent


def _validate(spec: AgentSpec, raw: object) -> tuple[Contract | None, list[str]]:
    if raw is None:
        return None, ["no structured output was returned"]
    try:
        if isinstance(raw, spec.out_contract):
            return raw, []
        data = raw.model_dump() if isinstance(raw, BaseModel) else raw
        return spec.out_contract.model_validate(data), []
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()]
        return None, problems


def _retry_message(problems: list[str]) -> dict[str, str]:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return {
        "role": "user",
        "content": f"Your previous output was rejected. Fix these problems and return the full output again:\n{listed}",
    }


def invoke_agent(
    spec: AgentSpec,
    packet: Packet,
    ctx: AgentContext,
    *,
    node: str,
    task_id: str | None = None,
) -> Contract:
    model = ctx.config.model_for(spec.role)
    log = CommandLog()
    tools: list[Callable[..., str]] = []
    if "shell" in spec.tools and ctx.workdir is not None:
        tools.append(make_shell_tool(ctx.workdir, ctx.config.shell, log, ctx.artifacts))
    agent = _resolve_factory(ctx)(spec, model, ctx.workdir, tools)

    messages: list[dict[str, str]] = [{"role": "user", "content": packet.render()}]
    problems: list[str] = []
    for attempt in (1, 2):
        name = artifact_name(node, task_id, attempt)
        if ctx.artifacts is not None:
            ctx.artifacts.write("packets", name, packet)
        payload_messages = messages if attempt == 1 else [*messages, _retry_message(problems)]
        started = time.monotonic()
        result = agent.invoke({"messages": payload_messages})  # Task 11: provider retries
        latency_ms = int((time.monotonic() - started) * 1000)
        output, problems = _validate(spec, result.get("structured_response"))
        outcome = "ok" if not problems else "invalid"
        # Task 10: evidence checks run here when output is not None.
        usage = extract_usage(result.get("messages", []))
        record(
            ctx.conn,
            TelemetryRow(
                run_id=ctx.run_id,
                layer=ctx.layer,
                node=node,
                role=spec.role,
                model=model,
                attempt=attempt,
                packet_tokens=packet.tokens,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=latency_ms,
                cost_usd=usage.cost_usd,
                outcome=outcome,
            ),
        )
        if output is not None and not problems:
            if ctx.artifacts is not None:
                ctx.artifacts.write("outputs", name, output)
            # Task 10: ledger and parking-lot side effects run here.
            return output
    raise ContractViolation(spec.name, problems)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agents -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/invoke.py tests/agents/conftest.py tests/agents/test_invoke.py
git commit -m "$(printf 'Add invoke_agent with contract validation, retry, and telemetry\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 10: Evidence checks, assumption ledger, and parking lot

**Files:**
- Create: `src/phil/agents/evidence.py`
- Modify: `src/phil/agents/invoke.py` (the two marked hook points)
- Test: `tests/agents/test_evidence.py`

**Interfaces:**
- Consumes: `invoke_agent`, `CommandLog` (Task 9 / Task 7); `ArtifactStore.append_assumptions`; `phil.store.parked.park`; `Ref`.
- Produces: `phil.agents.evidence.check_evidence(output: Contract, *, commands: list[str], workdir: Path | None) -> list[str]`. Problems: any `self_check.evidence` claim whose `command` (whitespace-stripped) was not run through the shell tool in this call; any `tests_added` path that does not exist under `workdir` (only when `workdir` is set). In `invoke_agent`: evidence problems make the attempt `evidence_fail` (retried like `invalid`); on success, `self_check.assumptions` are appended to the ledger (when artifacts exist) and each `self_check.out_of_scope` entry is parked with `raised_by=spec.name`, `why_not_now=f"out of scope for {node}"`, `source=Ref(label=f"{node} output", path=<output artifact path or "">)`, `run_id=ctx.run_id`.

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_evidence.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_evidence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.agents.evidence'`.

- [ ] **Step 3: Implement**

`src/phil/agents/evidence.py`:

```python
from pathlib import Path

from phil.contracts import Contract


def check_evidence(output: Contract, *, commands: list[str], workdir: Path | None) -> list[str]:
    problems: list[str] = []
    ran = {command.strip() for command in commands}
    self_check = getattr(output, "self_check", None)
    if self_check is not None:
        for claim in self_check.evidence:
            if claim.command and claim.command.strip() not in ran:
                problems.append(f"claimed command was never run: {claim.command}")
    if workdir is not None:
        for path in getattr(output, "tests_added", []):
            if not (workdir / path).exists():
                problems.append(f"tests_added file does not exist: {path}")
    return problems
```

In `src/phil/agents/invoke.py`, add imports:

```python
from phil.agents.evidence import check_evidence
from phil.contracts import Ref
from phil.store.parked import park
```

Replace the line `outcome = "ok" if not problems else "invalid"` and the comment below it with:

```python
        outcome = "ok" if not problems else "invalid"
        if output is not None:
            problems = check_evidence(output, commands=log.commands, workdir=ctx.workdir)
            if problems:
                outcome = "evidence_fail"
```

Replace the success block (from `if output is not None and not problems:` to `return output`) with:

```python
        if output is not None and not problems:
            output_path = ""
            if ctx.artifacts is not None:
                output_path = str(ctx.artifacts.write("outputs", name, output))
            _record_self_check(output, ctx, spec=spec, node=node, task_id=task_id, output_path=output_path)
            return output
```

and add this function above `invoke_agent`:

```python
def _record_self_check(
    output: Contract, ctx: AgentContext, *, spec: AgentSpec, node: str, task_id: str | None, output_path: str
) -> None:
    self_check = getattr(output, "self_check", None)
    if self_check is None:
        return
    if ctx.artifacts is not None and self_check.assumptions:
        ctx.artifacts.append_assumptions(node=node, task_id=task_id, assumptions=self_check.assumptions)
    for note in self_check.out_of_scope:
        park(
            ctx.conn,
            raised_by=spec.name,
            note=note,
            why_not_now=f"out of scope for {node}",
            source=Ref(label=f"{node} output", path=output_path),
            run_id=ctx.run_id,
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agents -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/evidence.py src/phil/agents/invoke.py tests/agents/test_evidence.py
git commit -m "$(printf 'Check agent evidence and record assumptions and parked items\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 11: Provider retries

**Files:**
- Create: `src/phil/agents/retry.py`
- Modify: `src/phil/agents/invoke.py` (the agent call)
- Test: `tests/agents/test_retry.py`

**Interfaces:**
- Consumes: `AgentContext.sleep`.
- Produces: `phil.agents.retry.TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}`; `is_transient(exc: BaseException) -> bool` (true for `TimeoutError`, `ConnectionError`, or an exception whose `status_code`, or whose `response.status_code`, is in `TRANSIENT_STATUS`); `call_with_retry(agent, payload: dict, *, sleep: Callable[[float], None], attempts: int = 3, base_delay: float = 1.0) -> dict` (backoff `base_delay * 2**i`; non-transient errors and the last failure are re-raised). In `invoke_agent`, an exception escaping `call_with_retry` is recorded as a telemetry row with `outcome="error"` (zero tokens) and then re-raised.

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_retry.py`:

```python
import pytest

from phil.agents.fake import FakeAgent, FakeAgentFactory
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.retry import call_with_retry, is_transient
from tests.agents.conftest import critique


class HTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


def test_is_transient():
    assert is_transient(HTTPError(429))
    assert is_transient(HTTPError(503))
    assert is_transient(TimeoutError())
    assert not is_transient(HTTPError(401))
    assert not is_transient(ValueError("bad"))


def test_retries_transient_errors_with_backoff():
    delays: list[float] = []
    agent = FakeAgent([HTTPError(429), HTTPError(503), "done"])
    result = call_with_retry(agent, {"messages": []}, sleep=delays.append)
    assert result["structured_response"] == "done"
    assert delays == [1.0, 2.0]


def test_gives_up_after_attempts():
    agent = FakeAgent([HTTPError(429), HTTPError(429), HTTPError(429)])
    with pytest.raises(HTTPError):
        call_with_retry(agent, {"messages": []}, sleep=lambda _: None)


def test_does_not_retry_permanent_errors():
    delays: list[float] = []
    agent = FakeAgent([HTTPError(401), "never"])
    with pytest.raises(HTTPError):
        call_with_retry(agent, {"messages": []}, sleep=delays.append)
    assert delays == []


def test_invoke_agent_retries_and_records_errors(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([HTTPError(429), critique()])
    ctx = AgentContext(
        config=config, conn=conn, layer="run", artifacts=artifacts, factory=factory, sleep=lambda _: None
    )
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    assert [row["outcome"] for row in conn.execute("SELECT outcome FROM telemetry")] == ["ok"]

    failing = FakeAgentFactory([HTTPError(401)])
    ctx.factory = failing
    with pytest.raises(HTTPError):
        invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    outcomes = [row["outcome"] for row in conn.execute("SELECT outcome FROM telemetry ORDER BY id")]
    assert outcomes == ["ok", "error"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_retry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.agents.retry'`.

- [ ] **Step 3: Implement**

`src/phil/agents/retry.py`:

```python
from collections.abc import Callable
from typing import Any

TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in TRANSIENT_STATUS


def call_with_retry(
    agent: Any,
    payload: dict,
    *,
    sleep: Callable[[float], None],
    attempts: int = 3,
    base_delay: float = 1.0,
) -> dict:
    for index in range(attempts):
        try:
            return agent.invoke(payload)
        except Exception as exc:
            if not is_transient(exc) or index == attempts - 1:
                raise
            sleep(base_delay * 2**index)
    raise AssertionError("unreachable")
```

In `src/phil/agents/invoke.py`, add `from phil.agents.retry import call_with_retry`, and replace

```python
        result = agent.invoke({"messages": payload_messages})  # Task 11: provider retries
        latency_ms = int((time.monotonic() - started) * 1000)
```

with:

```python
        try:
            result = call_with_retry(agent, {"messages": payload_messages}, sleep=ctx.sleep)
        except Exception:
            _record_error(ctx, spec=spec, model=model, node=node, attempt=attempt, packet=packet, started=started)
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
```

and add above `invoke_agent`:

```python
def _record_error(
    ctx: AgentContext, *, spec: AgentSpec, model: str, node: str, attempt: int, packet: Packet, started: float
) -> None:
    record(
        ctx.conn,
        TelemetryRow(
            run_id=ctx.run_id,
            layer=ctx.layer,
            node=node,
            role=spec.role,
            model=model,
            attempt=attempt,
            packet_tokens=packet.tokens,
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=0.0,
            outcome="error",
        ),
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agents -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/retry.py src/phil/agents/invoke.py tests/agents/test_retry.py
git commit -m "$(printf 'Retry transient provider errors and record failed calls\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 12: Real deepagents factory and live smoke test

**Files:**
- Create: `src/phil/agents/factory.py`, `tests/agents/test_factory.py`, `tests/live/__init__.py` (empty), `tests/live/test_invoke_live.py`
- Modify: `README.md` (live test instructions)

**Interfaces:**
- Consumes: `AgentSpec`, `load_prompt` (Task 6); `deepagents.create_deep_agent`, `deepagents.FilesystemPermission`, `deepagents.backends.filesystem.FilesystemBackend` (0.5.6).
- Produces: `phil.agents.factory.build_deep_agent(spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]])`, the default `AgentFactory`. Imports deepagents only inside the function body. Uses `response_format=spec.out_contract` and `system_prompt=load_prompt(spec)`. With a `workdir`, uses `FilesystemBackend(root_dir=workdir, virtual_mode=True)`. Permissions: always deny writes to `/.git/**` and `/phil.toml`; roles with `writes_files=False` are denied all writes (`/**`). `virtual_mode` is not a security boundary (deepagents threat model T9); the permission rules are.

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_factory.py`:

```python
import subprocess
import sys

from phil.agents.factory import build_deep_agent, filesystem_permissions
from phil.agents.registry import get_spec


def test_importing_invoke_does_not_load_llm_stack():
    code = (
        "import sys, phil.agents.invoke, phil.packets; "
        "heavy = ('deepagents', 'langchain', 'langchain_core', 'langgraph', 'langchain_openrouter'); "
        "print(','.join(m for m in heavy if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def rules(name):
    return [(rule.operations, rule.paths, rule.mode) for rule in filesystem_permissions(get_spec(name))]


def test_read_only_roles_cannot_write():
    assert (["write"], ["/**"], "deny") in rules("reviewer")
    assert (["write"], ["/**"], "deny") in rules("architect")


def test_writers_are_kept_out_of_git_and_config():
    implementer = rules("implementer")
    assert (["write"], ["/.git/**", "/phil.toml"], "deny") in implementer
    assert (["write"], ["/**"], "deny") not in implementer


def test_build_returns_invokable_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    agent = build_deep_agent(get_spec("critic"), "openrouter:poolside/laguna-m.1:free", tmp_path, [])
    assert hasattr(agent, "invoke")
```

`tests/live/test_invoke_live.py`:

```python
import os

import pytest

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.config import PhilConfig
from phil.contracts import CriticInput, Goal, Plan, PlanCritique, Task
from phil.packets import build_packet
from phil.store.db import connect

pytestmark = pytest.mark.live


def test_critic_returns_a_valid_critique(tmp_path):
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    task = Task(id="CALC-001", description="Add subtract(a, b) to calc.py", acceptance_criteria=["subtract(3, 1) == 2"])
    plan = Plan(keyword="CALC", description="Add a subtract function", tasks=[task], test_cmd="uv run pytest")
    packet = build_packet("critic", CriticInput(goal=Goal(objective="Add subtraction"), plan=plan), budget_tokens=4000)
    conn = connect(tmp_path / "phil.db")
    ctx = AgentContext(config=PhilConfig(), conn=conn, layer="chat")
    result = invoke_agent(get_spec("critic"), packet, ctx, node="critic")
    assert isinstance(result, PlanCritique)
    [row] = [dict(r) for r in conn.execute("SELECT * FROM telemetry WHERE outcome = 'ok'")]
    assert row["input_tokens"] > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.agents.factory'`.

- [ ] **Step 3: Implement** — `src/phil/agents/factory.py`:

```python
from collections.abc import Callable
from pathlib import Path
from typing import Any

from phil.agents.spec import AgentSpec, load_prompt


def filesystem_permissions(spec: AgentSpec) -> list[Any]:
    from deepagents import FilesystemPermission

    rules = [FilesystemPermission(operations=["write"], paths=["/.git/**", "/phil.toml"], mode="deny")]
    if not spec.writes_files:
        rules.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"))
    return rules


def build_deep_agent(
    spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]
) -> Any:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=workdir, virtual_mode=True) if workdir is not None else None
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=load_prompt(spec),
        backend=backend,
        permissions=filesystem_permissions(spec),
        response_format=spec.out_contract,
    )
```

Append to `README.md` under `## Development`:

```markdown
Live tests call a real model through OpenRouter and are skipped by default:

    set -a; source .env; set +a
    uv run pytest -m live
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -v`
Expected: all non-live tests PASS; `tests/live` is deselected.

Run (only if `OPENROUTER_API_KEY` is available): `set -a; source .env; set +a; uv run pytest -m live -v`
Expected: `test_critic_returns_a_valid_critique` PASS, or SKIP when the key is absent. Record the outcome in the report either way. A failure here caused by the free model's structured-output quality is a finding to report, not a reason to change the default model.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/factory.py tests/agents/test_factory.py tests/live README.md
git commit -m "$(printf 'Add deepagents factory with filesystem permissions and live smoke test\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

## Spec coverage for this plan

| Spec section / follow-up | Covered here | Deferred to |
|---|---|---|
| Follow-up: `GitError` pickling | Task 1 | — |
| Follow-up: DB schema versioning | Task 2 | — |
| Follow-up + §9: child-process environment, `pass_env` | Task 3 | — |
| Follow-up: contract field descriptions | Task 4 | `schema_version: Literal[1]`: not needed yet |
| §4.2 rule 2: `invoke_agent` is the only model call | Tasks 9–12 | — |
| §4.3 AgentSpec, prompts per role | Task 6 | Orchestrator spec and prompt: plan 4 |
| §5 contracts saved per step (packets and outputs) | Task 9 | — |
| §8 self-check, evidence spot-checks, assumption ledger | Tasks 4, 10 | Reviewer resolving ledger entries by id: plan 3 (assumption ids) |
| §9 packets, budgets, deterministic trimming, output offloading | Tasks 5, 7 | Per-role packet builders fed from run state: plan 3 |
| §9 terse register | Task 6 (`_shared.md`), Task 4 (descriptions) | — |
| §9a parking lot fed from `out_of_scope` | Task 10 | `/park`, `present()`: plan 4 |
| §10 provider retries, invalid output retry, evidence failure | Tasks 9–11 | `fallback_model` per role: add when a paid tier exists |
| §10 non-allowlisted command pauses for approval | Task 7 returns `DENIED` | Interrupt and approval via `attach`: plan 3 |
| §11 telemetry per call; eval hooks (standalone spec invocation, saved fixtures) | Tasks 8–12 | `phil runs --usage`, eval runner: later |
| §12 fake agents, live smoke test | Tasks 8, 12 | Graph tests: plan 3 |
| Filesystem safety (deepagents T9: `virtual_mode` not a boundary) | Task 12 permissions | Sandbox backends: future |
