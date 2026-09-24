# Phil Plan 3a: Run Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the run layer as an in-process LangGraph graph: worktree setup with a baseline test run, a per-task red→green TDD loop enforced by code gates, per-task commits, capped retries, human escalation (retry / skip / abort / approve commands / continue past budget), a tester pass with a product-code gate, reviewer rounds that turn issues into fix tasks, a run summary, and crash-safe resume from SQLite checkpoints.

**Architecture:** `RunEngine` owns the graph's node methods; `RunDeps` carries everything they need (config, db connection, repo, worktree path, artifact store, agent factory). Nodes that call agents go through `invoke_agent`; nodes that decide pass/fail are plain Python in `phil.run.gates`. The only `interrupt()` lives in the `escalate` node, which does nothing else, because LangGraph re-runs an interrupted node from the top on resume. State is plain JSON-able data (contracts are stored as `model_dump()` dicts). `phil.run.runner` starts, resumes, and continues runs. Plan 3b adds the detached worker process and CLI on top of `runner`.

**Tech Stack:** Python 3.14, LangGraph 1.1.10 (`StateGraph`, `interrupt`, `Command`), `langgraph-checkpoint-sqlite` 3.1.x (`SqliteSaver` on a `sqlite3` connection with `check_same_thread=False`, verified to resume across processes), pytest (real test runs inside temp repos), git.

**Spec:** `docs/superpowers/specs/2026-09-23-phil-v1-design.md` (§7 run graph, §8 cross-checks, §10 failures)
**Inputs:** `docs/superpowers/plans/2026-09-23-phil-02-followups.md` (Plan 3 section)

**Plan series:** 1 Foundation (done) → 2 Agent core (done) → **3a Run engine (this plan)** → 3b Worker + CLI → 4 Chat and interface → MVP lifecycle.

## Global Constraints

- Python `>=3.14`; `uv` for dependencies; source in `src/phil/`, tests in `tests/`; run `uv run pytest`.
- `phil.run.*` may import LangGraph at module level. `phil.cli.main`, `phil.agents.invoke`, and `phil.packets` must still not load `deepagents`, `langchain`, `langchain_core`, `langgraph`, or `langchain_openrouter` on import (existing tests enforce this).
- Agents are only ever called through `invoke_agent`. The test command is run by gate code (`phil.run.gates.run_tests`), never through an agent's shell tool.
- `interrupt()` is called only in `RunEngine.escalate`. No other node may call it.
- Graph state holds only JSON-able values (str, int, bool, None, list, dict). Store contracts as `model_dump()` dicts and rebuild with `model_validate`.
- Tests never touch the real `~/.phil` (autouse `phil_home`), never call a real model, and use `ScriptedAgentFactory` for agents.
- Default model stays `openrouter:nex-agi/nex-n2.5-pro:free` (`phil.config.DEFAULT_MODEL`).
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` after a blank line.

## File Structure

```
pyproject.toml                         modify: + langgraph-checkpoint-sqlite; − postgres/psycopg/deepagents-backends/dotenv/frontmatter
src/phil/run/__init__.py               create: empty
src/phil/run/checkpoint.py             create: open_checkpointer()
src/phil/run/gates.py                  create: is_test_path, parse_failures, run_tests, snapshot_tests, verify_red, verify_green
src/phil/run/state.py                  create: RunState, initial_state, load_plan, next_todo, with_task_status, issues_to_tasks, render_summary
src/phil/run/engine.py                 create: RunDeps, RunEngine (all nodes and routing)
src/phil/run/runner.py                 create: RunOutcome, start, resume, continue_run
src/phil/agents/tools.py               modify: CommandLog.denied
src/phil/agents/invoke.py              modify: AgentContext.command_log, AgentContext.extra_allow
src/phil/agents/fake.py                modify: Turn, ScriptedAgentFactory
src/phil/contracts/inputs.py           modify: ImplementInput.feedback
src/phil/prompts/implementer.md        modify: feedback instruction
src/phil/config.py                     modify: larger default budgets for tester/reviewer/architect
src/phil/store/artifacts.py            modify: write_text()
src/phil/workspace/worktree.py         modify: reset_to(), restore()
tests/run/__init__.py, tests/run/conftest.py, tests/run/test_*.py
```

---

### Task 1: Dependencies and the SQLite checkpointer

**Files:**
- Modify: `pyproject.toml` (via `uv`)
- Create: `src/phil/run/__init__.py` (empty), `src/phil/run/checkpoint.py`
- Test: `tests/run/__init__.py` (empty), `tests/run/test_checkpoint.py`

**Interfaces:**
- Produces: `phil.run.checkpoint.open_checkpointer(db_path: Path) -> SqliteSaver` (creates parent dirs; connection opened with `check_same_thread=False`).

- [ ] **Step 1: Update dependencies**

```bash
uv add langgraph-checkpoint-sqlite
uv remove langgraph-checkpoint-postgres psycopg deepagents-backends dotenv python-frontmatter
uv sync
```

Expected: `pyproject.toml` lists `langgraph-checkpoint-sqlite`; the removed packages are gone. (`prototype/` still imports `dotenv`/`frontmatter`; it is archived and not part of the package or tests.)

- [ ] **Step 2: Write the failing test** — `tests/run/test_checkpoint.py`:

```python
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from phil.run.checkpoint import open_checkpointer


class CounterState(TypedDict):
    n: int
    answers: list[str]


def build(db_path):
    def work(state: CounterState) -> dict:
        return {"n": state["n"] + 1}

    def ask(state: CounterState) -> dict:
        return {"answers": [*state["answers"], interrupt({"n": state["n"]})]}

    graph = StateGraph(CounterState)
    graph.add_node("work", work)
    graph.add_node("ask", ask)
    graph.add_edge(START, "work")
    graph.add_edge("work", "ask")
    graph.add_edge("ask", END)
    return graph.compile(checkpointer=open_checkpointer(db_path))


def test_interrupted_run_resumes_from_a_fresh_checkpointer(tmp_path):
    db_path = tmp_path / "nested" / "phil.db"
    config = {"configurable": {"thread_id": "r-0001"}}
    first = build(db_path)
    result = first.invoke({"n": 0, "answers": []}, config)
    assert result["__interrupt__"][0].value == {"n": 1}

    second = build(db_path)
    assert second.get_state(config).next == ("ask",)
    final = second.invoke(Command(resume="yes"), config)
    assert final == {"n": 1, "answers": ["yes"]}
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/run/test_checkpoint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.run'`.

- [ ] **Step 4: Implement** — `src/phil/run/checkpoint.py`:

```python
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def open_checkpointer(db_path: Path) -> SqliteSaver:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(db_path, check_same_thread=False))
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest -q`
Expected: all PASS (the existing lazy-import tests confirm `phil.cli.main` and `phil.agents.invoke` still do not load LangGraph).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/phil/run tests/run
git commit -m "$(printf 'Add SQLite checkpointer and drop prototype-only dependencies\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 2: `ScriptedAgentFactory` for role-scripted, side-effecting fakes

**Files:**
- Modify: `src/phil/agents/fake.py`
- Test: `tests/agents/test_scripted.py`

**Interfaces:**
- Consumes: `AgentSpec`, `FakeMessage`.
- Produces: dataclass `Turn(payload: dict, workdir: Path | None, tools: dict[str, Callable[..., str]])`; `ScriptedAgentFactory(scripts: dict[str, list[object]], usage: tuple[int, int, float] = (100, 20, 0.0))`, callable as `(spec, model, workdir, tools)`. Each role (spec name) pops its own script in order. A script item is an exception (raised), a callable `(Turn) -> object` (called; its return value is the structured response, so it can write files and call tools first), or a value (returned as-is). `.calls: list[tuple[str, dict]]` records `(role, payload)`; `.remaining() -> dict[str, int]`. An empty script raises `AssertionError("no scripted output left for <role>")`.

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_scripted.py`:

```python
import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.agents.registry import get_spec
from phil.agents.usage import extract_usage


def test_roles_have_independent_scripts(tmp_path):
    factory = ScriptedAgentFactory({"critic": ["c1"], "reviewer": ["r1", "r2"]})
    critic = factory(get_spec("critic"), "m", None, [])
    reviewer = factory(get_spec("reviewer"), "m", None, [])
    assert reviewer.invoke({"messages": []})["structured_response"] == "r1"
    assert critic.invoke({"messages": []})["structured_response"] == "c1"
    assert factory.remaining() == {"critic": 0, "reviewer": 1}
    assert [role for role, _ in factory.calls] == ["reviewer", "critic"]


def test_callable_items_get_workdir_tools_and_payload(tmp_path):
    def run_shell(command: str) -> str:
        return f"ran {command}"

    def script(turn: Turn) -> str:
        (turn.workdir / "made.txt").write_text("x")
        return turn.tools["run_shell"]("ls") + " / " + turn.payload["messages"][0]["content"]

    factory = ScriptedAgentFactory({"implementer": [script]}, usage=(7, 3, 0.1))
    agent = factory(get_spec("implementer"), "m", tmp_path, [run_shell])
    result = agent.invoke({"messages": [{"role": "user", "content": "hi"}]})
    assert result["structured_response"] == "ran ls / hi"
    assert (tmp_path / "made.txt").exists()
    usage = extract_usage(result["messages"])
    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd) == (7, 3, 0.1)


def test_exceptions_are_raised_and_empty_scripts_fail_clearly():
    factory = ScriptedAgentFactory({"critic": [RuntimeError("boom")]})
    agent = factory(get_spec("critic"), "m", None, [])
    with pytest.raises(RuntimeError, match="boom"):
        agent.invoke({"messages": []})
    with pytest.raises(AssertionError, match="no scripted output left for critic"):
        agent.invoke({"messages": []})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_scripted.py -v`
Expected: FAIL with `ImportError: cannot import name 'ScriptedAgentFactory'`.

- [ ] **Step 3: Implement** — in `src/phil/agents/fake.py`, add a module-level helper and replace the message construction inside `FakeAgent.invoke` with a call to it, then append the new classes:

```python
def _usage_message(usage: tuple[int, int, float]) -> FakeMessage:
    input_tokens, output_tokens, cost = usage
    return FakeMessage(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"cost": cost},
    )
```

`FakeAgent.invoke` ends with `return {"messages": [_usage_message(self.usage)], "structured_response": output}` (its behaviour is unchanged).

```python
@dataclass
class Turn:
    payload: dict
    workdir: Path | None
    tools: dict[str, Callable[..., str]]


class _ScriptedAgent:
    def __init__(
        self, factory: "ScriptedAgentFactory", role: str, workdir: Path | None, tools: dict[str, Callable[..., str]]
    ) -> None:
        self.factory = factory
        self.role = role
        self.workdir = workdir
        self.tools = tools

    def invoke(self, payload: dict) -> dict:
        script = self.factory.scripts.setdefault(self.role, [])
        if not script:
            raise AssertionError(f"no scripted output left for {self.role}")
        item = script.pop(0)
        self.factory.calls.append((self.role, payload))
        if isinstance(item, BaseException):
            raise item
        output = item(Turn(payload, self.workdir, self.tools)) if callable(item) else item
        return {"messages": [_usage_message(self.factory.usage)], "structured_response": output}


class ScriptedAgentFactory:
    def __init__(self, scripts: dict[str, list[object]], usage: tuple[int, int, float] = (100, 20, 0.0)) -> None:
        self.scripts = {role: list(items) for role, items in scripts.items()}
        self.usage = usage
        self.calls: list[tuple[str, dict]] = []

    def remaining(self) -> dict[str, int]:
        return {role: len(items) for role, items in self.scripts.items()}

    def __call__(
        self, spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]
    ) -> _ScriptedAgent:
        return _ScriptedAgent(self, spec.name, workdir, {tool.__name__: tool for tool in tools})
```

(Pydantic model instances are not callable, so returning a contract instance is treated as a value.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agents -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/fake.py tests/agents/test_scripted.py
git commit -m "$(printf 'Add ScriptedAgentFactory with per-role scripts and side effects\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 3: Denied commands, run-approved commands, and implementer feedback

**Files:**
- Modify: `src/phil/agents/tools.py`, `src/phil/agents/invoke.py`, `src/phil/contracts/inputs.py`, `src/phil/prompts/implementer.md`
- Test: `tests/agents/test_approvals.py`

**Interfaces:**
- Consumes: `ScriptedAgentFactory`, `Turn` (Task 2).
- Produces:
  - `CommandLog.denied: list[str]` — `run_shell` appends each denied command before returning `DENIED: ...`.
  - `AgentContext.command_log: CommandLog | None = None` — when set, `invoke_agent` uses it instead of a fresh log (callers read `.denied` after the call).
  - `AgentContext.extra_allow: tuple[str, ...] = ()` — exact commands approved for this run, appended to the shell allowlist for this call.
  - `ImplementInput.feedback: list[str] = []` — gate problems and human hints for the implementer.

- [ ] **Step 1: Write the failing tests** — `tests/agents/test_approvals.py`:

```python
import shlex
import sys

from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.tools import CommandLog
from phil.contracts import ImplementInput, SelfCheck, Task, TaskResult
from phil.packets import build_packet

PY = shlex.quote(sys.executable)


def result() -> TaskResult:
    check = SelfCheck(assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[])
    return TaskResult(phase="red", summary="s", files_changed=[], tests_added=[], self_check=check)


def packet(feedback=()):
    task = Task(id="CALC-001", description="d", acceptance_criteria=["c"])
    contract = ImplementInput(task=task, phase="red", test_cmd="pytest", feedback=list(feedback))
    return build_packet("implementer", contract, budget_tokens=4000)


def test_denied_commands_are_collected(config, conn, tmp_path):
    outputs: list[str] = []

    def script(turn: Turn) -> TaskResult:
        outputs.append(turn.tools["run_shell"]("make build"))
        return result()

    log = CommandLog()
    ctx = AgentContext(
        config=config, conn=conn, layer="run", workdir=tmp_path,
        factory=ScriptedAgentFactory({"implementer": [script]}), command_log=log,
    )
    invoke_agent(get_spec("implementer"), packet(), ctx, node="implement", task_id="CALC-001")
    assert outputs[0].startswith("DENIED:")
    assert log.denied == ["make build"]
    assert log.commands == []


def test_extra_allow_permits_an_approved_command(config, conn, tmp_path):
    (tmp_path / "build.py").write_text("print('built')\n")
    command = f"{PY} build.py"
    outputs: list[str] = []

    def script(turn: Turn) -> TaskResult:
        outputs.append(turn.tools["run_shell"](command))
        return result()

    log = CommandLog()
    ctx = AgentContext(
        config=config, conn=conn, layer="run", workdir=tmp_path,
        factory=ScriptedAgentFactory({"implementer": [script]}), command_log=log, extra_allow=(command,),
    )
    invoke_agent(get_spec("implementer"), packet(), ctx, node="implement", task_id="CALC-001")
    assert outputs[0].startswith("exit_code: 0")
    assert "built" in outputs[0]
    assert log.commands == [command]
    assert log.denied == []


def test_feedback_reaches_the_packet():
    assert "Human hint: use subtraction" in packet(["Human hint: use subtraction"]).render()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_approvals.py -v`
Expected: FAIL (`AgentContext` has no `command_log`; `ImplementInput` rejects `feedback`).

- [ ] **Step 3: Implement**

`src/phil/agents/tools.py`: add `denied: list[str] = field(default_factory=list)` to `CommandLog`, and in `run_shell` append before returning the denial:

```python
        if not policy.is_allowed(command):
            log.denied.append(command)
            allowed = ", ".join(shell.allow)
            return f"DENIED: `{command}` is not on the allowlist. Allowed patterns: {allowed}"
```

`src/phil/agents/invoke.py`: add two fields at the end of `AgentContext`:

```python
    command_log: CommandLog | None = None
    extra_allow: tuple[str, ...] = ()
```

and in `invoke_agent` replace `log = CommandLog()` and the `make_shell_tool(...)` call with:

```python
    log = ctx.command_log if ctx.command_log is not None else CommandLog()
    shell = ctx.config.shell
    if ctx.extra_allow:
        shell = shell.model_copy(update={"allow": [*shell.allow, *ctx.extra_allow]})
```

```python
        tools.append(make_shell_tool(ctx.workdir, shell, log, ctx.artifacts, log_prefix=log_prefix))
```

`src/phil/contracts/inputs.py`: add to `ImplementInput` after `last_report`:

```python
    feedback: list[str] = Field(default_factory=list)
```

`src/phil/prompts/implementer.md`: in the `## Rules` list, add as the first bullet:

```markdown
- If your input includes `feedback`, address every item first. It lists why your previous attempt was rejected, or a hint from a human.
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/tools.py src/phil/agents/invoke.py src/phil/contracts/inputs.py src/phil/prompts/implementer.md tests/agents/test_approvals.py
git commit -m "$(printf 'Collect denied commands, allow run-approved ones, and pass feedback to the implementer\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 4: Test runs and failure parsing

**Files:**
- Create: `src/phil/run/gates.py`
- Test: `tests/run/test_gates_tests.py`

**Interfaces:**
- Consumes: `run_command`, `child_env` (`phil.workspace.shell`), `ShellConfig`, `ArtifactStore`, `TestReport`.
- Produces: `is_test_path(path: str, globs: list[str]) -> bool` (a glob matches the full repo-relative path or the file name); `parse_failures(output: str, exit_code: int, timed_out: bool = False) -> list[str]` (pytest-style `FAILED <id>` / `ERROR <id>` ids in order, deduplicated; `["timed out"]` on timeout; `[f"exit code {n}"]` when the run failed but nothing parsed); `run_tests(test_cmd: str, worktree: Path, *, shell: ShellConfig, artifacts: ArtifactStore | None, name: str, baseline: list[str] = ()) -> TestReport` (runs with secrets stripped and `PYTHONDONTWRITEBYTECODE=1`, saves the full log as `logs/<name>.log`, keeps at most 50 failures, and fills `new_failures_vs_baseline`).

- [ ] **Step 1: Write the failing tests** — `tests/run/test_gates_tests.py`:

```python
import shlex
import sys

from phil.config import ShellConfig
from phil.run.gates import is_test_path, parse_failures, run_tests
from phil.store.artifacts import ArtifactStore

TEST_CMD = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"
GLOBS = ["tests/*", "test/*", "test_*.py", "*_test.py"]


def test_is_test_path_matches_full_path_or_name():
    assert is_test_path("tests/test_calc.py", GLOBS)
    assert is_test_path("pkg/test_util.py", GLOBS)
    assert is_test_path("tests/unit/helpers.py", GLOBS)
    assert not is_test_path("calc.py", GLOBS)
    assert not is_test_path("src/testing.py", GLOBS)


def test_parse_failures():
    output = (
        "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
        "ERROR tests/test_b.py - ImportError: cannot import name 'subtract'\n"
        "FAILED tests/test_a.py::test_x - duplicate line\n"
    )
    assert parse_failures(output, 1) == ["tests/test_a.py::test_x", "tests/test_b.py"]
    assert parse_failures("", 0) == []
    assert parse_failures("no tests ran", 5) == ["exit code 5"]
    assert parse_failures("", -9, timed_out=True) == ["timed out"]


def make_project(root, test_body):
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text(f"from calc import add\n\n\ndef test_add():\n    {test_body}\n")


def test_run_tests_passing(tmp_path):
    make_project(tmp_path, "assert add(1, 2) == 3")
    artifacts = ArtifactStore(tmp_path / "run")
    report = run_tests(TEST_CMD, tmp_path, shell=ShellConfig(), artifacts=artifacts, name="baseline")
    assert report.passed
    assert report.failures == []
    assert (tmp_path / "run" / "logs" / "baseline.log").exists()
    assert not list(tmp_path.rglob("__pycache__"))


def test_run_tests_failing_with_baseline(tmp_path):
    make_project(tmp_path, "assert add(1, 2) == 4")
    report = run_tests(
        TEST_CMD, tmp_path, shell=ShellConfig(), artifacts=None, name="v",
        baseline=["tests/test_calc.py::test_add"],
    )
    assert not report.passed
    assert report.failures == ["tests/test_calc.py::test_add"]
    assert report.new_failures_vs_baseline == []
    assert report.log_path == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_gates_tests.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.run.gates'`.

- [ ] **Step 3: Implement** — `src/phil/run/gates.py`:

```python
import fnmatch
import os
import re
from pathlib import Path, PurePosixPath

from phil.config import ShellConfig
from phil.contracts import TestReport
from phil.store.artifacts import ArtifactStore
from phil.workspace.shell import child_env, run_command

_FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
MAX_FAILURES = 50


def is_test_path(path: str, globs: list[str]) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatchcase(path, glob) or fnmatch.fnmatchcase(name, glob) for glob in globs)


def parse_failures(output: str, exit_code: int, timed_out: bool = False) -> list[str]:
    if timed_out:
        return ["timed out"]
    ids = list(dict.fromkeys(_FAILURE_LINE.findall(output)))
    if exit_code != 0 and not ids:
        return [f"exit code {exit_code}"]
    return ids


def run_tests(
    test_cmd: str,
    worktree: Path,
    *,
    shell: ShellConfig,
    artifacts: ArtifactStore | None,
    name: str,
    baseline: list[str] = (),
) -> TestReport:
    env = child_env(os.environ, shell.pass_env) | {"PYTHONDONTWRITEBYTECODE": "1"}
    result = run_command(test_cmd, worktree, shell.timeout_s, env=env)
    output = result.stdout + (f"\n{result.stderr}" if result.stderr else "")
    failures = parse_failures(output, result.exit_code, result.timed_out)[:MAX_FAILURES]
    log_path = str(artifacts.write_log(name, output)) if artifacts is not None else ""
    known = set(baseline)
    return TestReport(
        command=test_cmd,
        passed=result.ok,
        failures=failures,
        log_path=log_path,
        new_failures_vs_baseline=[failure for failure in failures if failure not in known],
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/run -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/gates.py tests/run/test_gates_tests.py
git commit -m "$(printf 'Run project tests and parse failures for the gates\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 5: Red/green gates and worktree restore helpers

**Files:**
- Modify: `src/phil/run/gates.py`, `src/phil/workspace/worktree.py`
- Test: `tests/run/test_gates_verify.py`, `tests/workspace/test_worktree.py` (append)

**Interfaces:**
- Consumes: `is_test_path`, `TestReport` (Task 4); `phil.git.git`, `GitError`.
- Produces:
  - `snapshot_tests(worktree: Path, changed: list[str], globs: list[str]) -> dict[str, str]` — for each changed test path, its SHA-256 or `"<deleted>"`.
  - `verify_red(changed: list[str], report: TestReport, globs: list[str]) -> list[str]` — problems: non-test files changed; no test file changed; no new failing tests versus the baseline.
  - `verify_green(report: TestReport, red_snapshot: dict[str, str], now_snapshot: dict[str, str]) -> list[str]` — problems: new failures remain; any test file differs from the red snapshot.
  - `WorktreeManager.reset_to(path: Path, sha: str) -> None` (`git reset --hard <sha>` then `git clean -fd`).
  - `WorktreeManager.restore(path: Path, paths: list[str]) -> None` — tracked paths are checked out from HEAD; untracked ones are deleted.

- [ ] **Step 1: Write the failing tests**

`tests/run/test_gates_verify.py`:

```python
from phil.contracts import TestReport
from phil.run.gates import snapshot_tests, verify_green, verify_red

GLOBS = ["tests/*", "test_*.py"]


def report(new=(), failures=()) -> TestReport:
    return TestReport(
        command="pytest", passed=not failures, failures=list(failures), log_path="", new_failures_vs_baseline=list(new)
    )


def test_red_passes_with_new_failing_tests_only():
    assert verify_red(["tests/test_sub.py"], report(new=["tests/test_sub.py"], failures=["tests/test_sub.py"]), GLOBS) == []


def test_red_rejects_product_changes_missing_tests_and_passing_tests():
    problems = verify_red(["calc.py"], report(), GLOBS)
    assert problems == [
        "red phase changed non-test files: calc.py",
        "red phase added or changed no test files",
        "no new failing tests compared with the baseline; red phase needs tests that fail",
    ]


def test_green_passes_when_tests_pass_and_are_untouched():
    snap = {"tests/test_sub.py": "abc"}
    assert verify_green(report(), snap, dict(snap)) == []


def test_green_rejects_failures_and_test_edits():
    problems = verify_green(
        report(new=["tests/test_sub.py::test_subtract"], failures=["tests/test_sub.py::test_subtract"]),
        {"tests/test_sub.py": "abc"},
        {"tests/test_sub.py": "def", "tests/test_new.py": "123"},
    )
    assert problems == [
        "tests still failing: tests/test_sub.py::test_subtract",
        "green phase modified test files: tests/test_new.py, tests/test_sub.py",
    ]


def test_snapshot_hashes_test_files_and_marks_deletions(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x")
    snap = snapshot_tests(tmp_path, ["tests/test_a.py", "tests/test_gone.py", "calc.py"], GLOBS)
    assert set(snap) == {"tests/test_a.py", "tests/test_gone.py"}
    assert len(snap["tests/test_a.py"]) == 64
    assert snap["tests/test_gone.py"] == "<deleted>"
```

Append to `tests/workspace/test_worktree.py`:

```python
def test_reset_to_discards_changes_and_untracked_files(setup):
    manager, worktree, base = setup
    (worktree.path / "app.py").write_text("changed\n")
    (worktree.path / "new.py").write_text("x = 1\n")
    manager.reset_to(worktree.path, base)
    assert (worktree.path / "app.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert not (worktree.path / "new.py").exists()


def test_restore_reverts_tracked_and_removes_untracked(setup):
    manager, worktree, _ = setup
    (worktree.path / "app.py").write_text("changed\n")
    (worktree.path / "extra.py").write_text("x = 1\n")
    (worktree.path / "keep.py").write_text("y = 2\n")
    manager.restore(worktree.path, ["app.py", "extra.py"])
    assert (worktree.path / "app.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert not (worktree.path / "extra.py").exists()
    assert (worktree.path / "keep.py").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_gates_verify.py tests/workspace/test_worktree.py -v`
Expected: FAIL (`ImportError` for `snapshot_tests`; `WorktreeManager` has no `reset_to`).

- [ ] **Step 3: Implement**

Append to `src/phil/run/gates.py` (add `import hashlib` to the imports):

```python
DELETED = "<deleted>"


def snapshot_tests(worktree: Path, changed: list[str], globs: list[str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in changed:
        if not is_test_path(path, globs):
            continue
        target = worktree / path
        snapshot[path] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else DELETED
    return snapshot


def verify_red(changed: list[str], report: TestReport, globs: list[str]) -> list[str]:
    problems: list[str] = []
    non_test = [path for path in changed if not is_test_path(path, globs)]
    if non_test:
        problems.append(f"red phase changed non-test files: {', '.join(non_test)}")
    if not any(is_test_path(path, globs) for path in changed):
        problems.append("red phase added or changed no test files")
    if not report.new_failures_vs_baseline:
        problems.append("no new failing tests compared with the baseline; red phase needs tests that fail")
    return problems


def verify_green(report: TestReport, red_snapshot: dict[str, str], now_snapshot: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if report.new_failures_vs_baseline:
        problems.append(f"tests still failing: {', '.join(report.new_failures_vs_baseline)}")
    edited = sorted(path for path in set(red_snapshot) | set(now_snapshot) if red_snapshot.get(path) != now_snapshot.get(path))
    if edited:
        problems.append(f"green phase modified test files: {', '.join(edited)}")
    return problems
```

Append to `WorktreeManager` in `src/phil/workspace/worktree.py` (add `GitError` to the existing `from phil.git import ...` line):

```python
    def reset_to(self, path: Path, sha: str) -> None:
        git(path, "reset", "--hard", sha)
        git(path, "clean", "-fd")

    def restore(self, path: Path, paths: list[str]) -> None:
        for relative in paths:
            try:
                git(path, "cat-file", "-e", f"HEAD:{relative}")
            except GitError:
                target = path / relative
                if target.is_file():
                    target.unlink()
                continue
            git(path, "checkout", "HEAD", "--", relative)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/run tests/workspace -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/gates.py src/phil/workspace/worktree.py tests/run/test_gates_verify.py tests/workspace/test_worktree.py
git commit -m "$(printf 'Add red/green gates and worktree reset and restore\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 6: Run state, plan helpers, and the run summary

**Files:**
- Create: `src/phil/run/state.py`
- Test: `tests/run/test_state.py`

**Interfaces:**
- Consumes: `Plan`, `Task`, `Issue`.
- Produces: `RunState` (`TypedDict`, `total=False`, keys listed below); `initial_state(run_id: str, plan: Plan, base_sha: str, test_cmd: str) -> RunState`; `load_plan(state) -> Plan`; `next_todo(plan) -> int | None`; `with_task_status(plan, index: int, status: str) -> Plan`; `issues_to_tasks(plan, issues: list[Issue], source: str) -> Plan`; `render_summary(*, run_id: str, plan: Plan, status: str, branch: str, base_sha: str, head_sha: str, open_issues: list[dict]) -> str`.

State keys: `run_id, plan, base_sha, test_cmd, status, baseline_failures, task_index, task_base_sha, phase, attempts, call_seq, last_problems, last_report, red_snapshot, hint, implement_failed, denied, approved, escalation, verdict, next, tester_done, review_rounds, budget_override, original_task_ids, open_issues`.

- [ ] **Step 1: Write the failing tests** — `tests/run/test_state.py`:

```python
from phil.contracts import Issue, Plan, Task
from phil.run.state import initial_state, issues_to_tasks, load_plan, next_todo, render_summary, with_task_status


def plan() -> Plan:
    tasks = [
        Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"]),
        Task(id="CALC-002", description="Add multiply", acceptance_criteria=["multiply(2, 3) == 6"]),
    ]
    return Plan(keyword="CALC", description="d", tasks=tasks, test_cmd="pytest")


def test_initial_state_round_trips_the_plan():
    state = initial_state("r-0001", plan(), "abc", "pytest")
    assert load_plan(state) == plan()
    assert state["original_task_ids"] == ["CALC-001", "CALC-002"]
    assert (state["call_seq"], state["approved"], state["open_issues"], state["status"]) == (0, [], [], "pending")


def test_next_todo_and_status_updates():
    current = plan()
    assert next_todo(current) == 0
    current = with_task_status(current, 0, "DONE")
    assert next_todo(current) == 1
    current = with_task_status(current, 1, "SKIPPED")
    assert next_todo(current) is None
    assert plan().tasks[0].status == "TODO"


def test_issues_become_numbered_fix_tasks():
    issues = [
        Issue(severity="major", note="subtract ignores floats", file="calc.py"),
        Issue(severity="blocker", note="missing negative test"),
    ]
    updated = issues_to_tasks(plan(), issues, "review")
    new = updated.tasks[2:]
    assert [t.id for t in new] == ["CALC-003", "CALC-004"]
    assert new[0].description == "Fix (review): subtract ignores floats"
    assert new[0].acceptance_criteria == ["subtract ignores floats"]
    assert new[0].files_hint == ["calc.py"]
    assert new[1].files_hint == []


def test_render_summary_lists_tasks_and_issues():
    current = with_task_status(with_task_status(plan(), 0, "DONE"), 1, "SKIPPED")
    text = render_summary(
        run_id="r-0001", plan=current, status="completed", branch="phil/r-0001",
        base_sha="aaaaaaaa1111", head_sha="bbbbbbbb2222",
        open_issues=[{"severity": "minor", "note": "rename helper", "file": "calc.py", "line": 3, "task_id": None}],
    )
    assert text.startswith("# Run r-0001 · CALC\n")
    assert "Status: completed · branch phil/r-0001 · aaaaaaaa..bbbbbbbb" in text
    assert "- [x] CALC-001 Add subtract" in text
    assert "- [ ] CALC-002 Add multiply (SKIPPED)" in text
    assert "- (minor) rename helper [calc.py:3]" in text
    assert "## Open issues\n- (none)" in render_summary(
        run_id="r-0001", plan=plan(), status="completed", branch="b", base_sha="a" * 12, head_sha="b" * 12, open_issues=[]
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.run.state'`.

- [ ] **Step 3: Implement** — `src/phil/run/state.py`:

```python
from typing import Any, TypedDict

from phil.contracts import Issue, Plan, Task


class RunState(TypedDict, total=False):
    run_id: str
    plan: dict[str, Any]
    base_sha: str
    test_cmd: str
    status: str
    baseline_failures: list[str]
    task_index: int
    task_base_sha: str
    phase: str
    attempts: int
    call_seq: int
    last_problems: list[str]
    last_report: dict[str, Any] | None
    red_snapshot: dict[str, str]
    hint: str | None
    implement_failed: bool
    denied: list[str]
    approved: list[str]
    escalation: dict[str, Any] | None
    verdict: str
    next: str
    tester_done: bool
    review_rounds: int
    budget_override: bool
    original_task_ids: list[str]
    open_issues: list[dict[str, Any]]


def initial_state(run_id: str, plan: Plan, base_sha: str, test_cmd: str) -> RunState:
    return RunState(
        run_id=run_id,
        plan=plan.model_dump(),
        base_sha=base_sha,
        test_cmd=test_cmd,
        status="pending",
        baseline_failures=[],
        task_index=-1,
        task_base_sha=base_sha,
        phase="red",
        attempts=0,
        call_seq=0,
        last_problems=[],
        last_report=None,
        red_snapshot={},
        hint=None,
        implement_failed=False,
        denied=[],
        approved=[],
        escalation=None,
        verdict="",
        next="",
        tester_done=False,
        review_rounds=0,
        budget_override=False,
        original_task_ids=[task.id for task in plan.tasks],
        open_issues=[],
    )


def load_plan(state: RunState) -> Plan:
    return Plan.model_validate(state["plan"])


def next_todo(plan: Plan) -> int | None:
    for index, task in enumerate(plan.tasks):
        if task.status == "TODO":
            return index
    return None


def with_task_status(plan: Plan, index: int, status: str) -> Plan:
    tasks = list(plan.tasks)
    tasks[index] = tasks[index].model_copy(update={"status": status})
    return plan.model_copy(update={"tasks": tasks})


def issues_to_tasks(plan: Plan, issues: list[Issue], source: str) -> Plan:
    number = max(int(task.id.rsplit("-", 1)[1]) for task in plan.tasks)
    new_tasks: list[Task] = []
    for issue in issues:
        number += 1
        note = issue.note.strip()
        new_tasks.append(
            Task(
                id=f"{plan.keyword}-{number:03d}",
                description=f"Fix ({source}): {note}",
                acceptance_criteria=[note],
                files_hint=[issue.file] if issue.file else [],
            )
        )
    return plan.model_copy(update={"tasks": [*plan.tasks, *new_tasks]})


def _issue_line(issue: dict[str, Any]) -> str:
    location = ""
    if issue.get("file"):
        location = f" [{issue['file']}" + (f":{issue['line']}" if issue.get("line") else "") + "]"
    return f"- ({issue['severity']}) {issue['note']}{location}"


def render_summary(
    *,
    run_id: str,
    plan: Plan,
    status: str,
    branch: str,
    base_sha: str,
    head_sha: str,
    open_issues: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Run {run_id} · {plan.keyword}",
        "",
        f"Status: {status} · branch {branch} · {base_sha[:8]}..{head_sha[:8]}",
        "",
        "## Tasks",
    ]
    for task in plan.tasks:
        mark = "x" if task.status == "DONE" else " "
        suffix = "" if task.status in ("DONE", "TODO") else f" ({task.status})"
        lines.append(f"- [{mark}] {task.id} {task.description}{suffix}")
    lines += ["", "## Open issues"]
    lines += [_issue_line(issue) for issue in open_issues] or ["- (none)"]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/run/test_state.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/state.py tests/run/test_state.py
git commit -m "$(printf 'Add run state, plan helpers, and run summary\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 7: Engine happy path — setup, red, green, commit, finish

**Files:**
- Create: `src/phil/run/engine.py`, `tests/run/conftest.py`, `tests/run/test_engine_happy.py`
- Modify: `src/phil/store/artifacts.py` (`write_text`)

**Interfaces:**
- Consumes: everything above; `invoke_agent`, `AgentContext`, `ContractViolation`, `get_spec`, `build_packet`, `WorktreeManager`, `update_run`, `ArtifactStore`.
- Produces:
  - `ArtifactStore.write_text(relative: str, text: str) -> Path`.
  - dataclass `RunDeps(config: PhilConfig, conn: sqlite3.Connection, repo_root: Path, run_id: str, worktree: Path, artifacts: ArtifactStore, factory: AgentFactory | None = None, sleep: Callable[[float], None] = time.sleep)`.
  - `RunEngine(deps)` with `build(checkpointer) -> CompiledStateGraph` and node methods `setup`, `pick_task`, `implement`, `verify`, `commit`, `finish`; the branch is `branch_for(run_id)`.
  - Test fixtures in `tests/run/conftest.py`: `calc_repo`, `make_harness`, and helpers `write_red`, `write_green`, `bad_green`, `calc_plan`, `task_result`, `self_check`, `tester_report`, `review`, `TEST_CMD`. (`tester_report` and `review` are used from Task 10 on.)

- [ ] **Step 1: Write the fixtures** — `tests/run/conftest.py`:

```python
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
```

- [ ] **Step 2: Write the failing test** — `tests/run/test_engine_happy.py`:

```python
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import write_green, write_red


def test_one_task_runs_red_green_and_commits(make_harness, calc_repo):
    harness = make_harness({"implementer": [write_red, write_green]})
    final = harness.start()

    assert final["status"] == "completed"
    assert load_plan(final).tasks[0].status == "DONE"
    log = run_git(calc_repo, "log", "--format=%s", "phil/r-0001")
    assert log.splitlines()[0] == "CALC-001: Add subtract"
    assert "def subtract" in (harness.deps.worktree / "calc.py").read_text()
    assert "subtract" not in (calc_repo / "calc.py").read_text()
    record = harness.run_record()
    assert (record.state, record.tasks_done, record.tasks_total) == ("completed", 1, 1)
    summary = (harness.deps.artifacts.run_dir / "summary.md").read_text()
    assert "- [x] CALC-001 Add subtract" in summary
    assert harness.factory.remaining() == {"implementer": 0}


def test_green_phase_receives_the_red_test_report(make_harness):
    harness = make_harness({"implementer": [write_red, write_green]})
    harness.start()
    green_payload = [payload for role, payload in harness.factory.calls if role == "implementer"][1]
    packet_text = green_payload["messages"][0]["content"]
    assert '"phase": "green"' in packet_text
    assert "tests/test_sub.py" in packet_text
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/run/test_engine_happy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'phil.run.engine'`.

- [ ] **Step 4: Implement**

Add to `ArtifactStore` in `src/phil/store/artifacts.py`:

```python
    def write_text(self, relative: str, text: str) -> Path:
        path = self._file(relative)
        path.write_text(text)
        return path
```

`src/phil/run/engine.py`:

```python
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from phil.agents.invoke import AgentContext, AgentFactory, ContractViolation, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.tools import CommandLog
from phil.config import PhilConfig
from phil.contracts import ImplementInput, TestReport
from phil.git import branch_for
from phil.packets import build_packet
from phil.run.gates import run_tests, snapshot_tests, verify_green, verify_red
from phil.run.state import RunState, load_plan, next_todo, render_summary, with_task_status
from phil.store.artifacts import ArtifactStore, artifact_name
from phil.store.runs import update_run
from phil.workspace.worktree import WorktreeManager


@dataclass
class RunDeps:
    config: PhilConfig
    conn: sqlite3.Connection
    repo_root: Path
    run_id: str
    worktree: Path
    artifacts: ArtifactStore
    factory: AgentFactory | None = None
    sleep: Callable[[float], None] = time.sleep


class RunEngine:
    def __init__(self, deps: RunDeps) -> None:
        self.deps = deps
        self.worktrees = WorktreeManager(deps.repo_root)

    # --- graph -------------------------------------------------------------

    def build(self, checkpointer: Any) -> Any:
        graph = StateGraph(RunState)
        graph.add_node("setup", self.setup)
        graph.add_node("pick_task", self.pick_task)
        graph.add_node("implement", self.implement)
        graph.add_node("verify", self.verify)
        graph.add_node("commit", self.commit)
        graph.add_node("finish", self.finish)
        graph.add_edge(START, "setup")
        graph.add_edge("setup", "pick_task")
        graph.add_conditional_edges("pick_task", self.route_after_pick, ["implement", "finish"])
        graph.add_edge("implement", "verify")
        graph.add_conditional_edges("verify", self.route_after_verify, ["implement", "commit"])
        graph.add_edge("commit", "pick_task")
        graph.add_edge("finish", END)
        return graph.compile(checkpointer=checkpointer)

    def route_after_pick(self, state: RunState) -> str:
        return "implement" if state["task_index"] >= 0 else "finish"

    def route_after_verify(self, state: RunState) -> str:
        return {"red_ok": "implement", "green_ok": "commit", "retry": "implement"}[state["verdict"]]

    # --- helpers -----------------------------------------------------------

    def _update_run(self, **fields: object) -> None:
        update_run(self.deps.conn, self.deps.run_id, **fields)

    def _test(self, state: RunState, name: str) -> TestReport:
        return run_tests(
            state["test_cmd"],
            self.deps.worktree,
            shell=self.deps.config.shell,
            artifacts=self.deps.artifacts,
            name=name,
            baseline=state.get("baseline_failures", []),
        )

    def _context(self, state: RunState, log: CommandLog) -> AgentContext:
        return AgentContext(
            config=self.deps.config,
            conn=self.deps.conn,
            layer="run",
            run_id=self.deps.run_id,
            artifacts=self.deps.artifacts,
            workdir=self.deps.worktree,
            factory=self.deps.factory,
            sleep=self.deps.sleep,
            command_log=log,
            extra_allow=tuple(state.get("approved", [])),
        )

    def _budget(self, role: str) -> int:
        return self.deps.config.budget_for(role).max_input_tokens

    # --- nodes -------------------------------------------------------------

    def setup(self, state: RunState) -> dict:
        plan = load_plan(state)
        if not self.deps.worktree.exists():
            self.worktrees.create(run_id=self.deps.run_id, base_sha=state["base_sha"], path=self.deps.worktree)
        self.deps.artifacts.write_plan(plan)
        baseline = self._test({**state, "baseline_failures": []}, "baseline")
        self._update_run(state="running", current_node="setup", tasks_total=len(plan.tasks))
        return {"baseline_failures": baseline.failures, "status": "running"}

    def pick_task(self, state: RunState) -> dict:
        index = next_todo(load_plan(state))
        self._update_run(current_node="pick_task")
        if index is None:
            return {"task_index": -1}
        return {
            "task_index": index,
            "task_base_sha": self.worktrees.head(self.deps.worktree),
            "phase": "red",
            "attempts": 0,
            "last_problems": [],
            "last_report": None,
            "red_snapshot": {},
            "hint": None,
            "implement_failed": False,
            "denied": [],
            "escalation": None,
        }

    def implement(self, state: RunState) -> dict:
        plan = load_plan(state)
        task = plan.tasks[state["task_index"]]
        seq = state.get("call_seq", 0) + 1
        feedback = list(state.get("last_problems", []))
        if state.get("hint"):
            feedback.append(f"Human hint: {state['hint']}")
        last = state.get("last_report")
        contract = ImplementInput(
            task=task,
            phase=state["phase"],
            test_cmd=state["test_cmd"],
            last_report=TestReport.model_validate(last) if last else None,
            feedback=feedback,
        )
        ledger = [e["assumption"] for e in self.deps.artifacts.read_assumptions() if e.get("task_id") == task.id]
        packet = build_packet(
            "implementer",
            contract,
            budget_tokens=self._budget("implementer"),
            root=self.deps.worktree,
            files=task.files_hint,
            ledger=ledger,
        )
        log = CommandLog()
        self._update_run(current_node="implement")
        try:
            invoke_agent(
                get_spec("implementer"), packet, self._context(state, log), node="implement", task_id=task.id, call=seq
            )
            failed, problems = False, []
        except ContractViolation as exc:
            failed, problems = True, [f"implementer output rejected: {problem}" for problem in exc.problems]
        return {"call_seq": seq, "implement_failed": failed, "last_problems": problems, "denied": list(log.denied)}

    def verify(self, state: RunState) -> dict:
        task = load_plan(state).tasks[state["task_index"]]
        globs = self.deps.config.project.test_globs
        worktree = self.deps.worktree
        self._update_run(current_node="verify")
        if state.get("implement_failed"):
            return self._failed_attempt(state, state.get("last_problems", []), state.get("last_report"))
        changed = self.worktrees.changed_files(worktree, since=state["task_base_sha"])
        report = self._test(state, artifact_name("verify", task.id, state["call_seq"]))
        if state["phase"] == "red":
            problems = verify_red(changed, report, globs)
            if not problems:
                return {
                    "phase": "green",
                    "attempts": 0,
                    "last_report": report.model_dump(),
                    "last_problems": [],
                    "red_snapshot": snapshot_tests(worktree, changed, globs),
                    "verdict": "red_ok",
                }
        else:
            problems = verify_green(report, state.get("red_snapshot", {}), snapshot_tests(worktree, changed, globs))
            if not problems:
                return {"last_report": report.model_dump(), "last_problems": [], "verdict": "green_ok"}
        return self._failed_attempt(state, problems, report.model_dump())

    def _failed_attempt(self, state: RunState, problems: list[str], report: dict | None) -> dict:
        return {
            "attempts": state.get("attempts", 0) + 1,
            "last_problems": problems,
            "last_report": report,
            "verdict": "retry",
        }

    def commit(self, state: RunState) -> dict:
        plan = load_plan(state)
        index = state["task_index"]
        task = plan.tasks[index]
        worktree = self.deps.worktree
        if self.worktrees.changed_files(worktree, since=self.worktrees.head(worktree)):
            self.worktrees.commit_all(worktree, f"{task.id}: {task.description}")
        plan = with_task_status(plan, index, "DONE")
        done = sum(1 for item in plan.tasks if item.status == "DONE")
        self._update_run(current_node="commit", tasks_done=done, tasks_total=len(plan.tasks))
        return {"plan": plan.model_dump()}

    def finish(self, state: RunState) -> dict:
        plan = load_plan(state)
        status = "aborted" if state.get("status") == "aborted" else "completed"
        summary = render_summary(
            run_id=self.deps.run_id,
            plan=plan,
            status=status,
            branch=branch_for(self.deps.run_id),
            base_sha=state["base_sha"],
            head_sha=self.worktrees.head(self.deps.worktree),
            open_issues=state.get("open_issues", []),
        )
        self.deps.artifacts.write_text("summary.md", summary)
        self._update_run(state=status, current_node="finish", needs_attention=None)
        return {"status": status}
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/run -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/phil/run/engine.py src/phil/store/artifacts.py tests/run/conftest.py tests/run/test_engine_happy.py
git commit -m "$(printf 'Add run engine happy path: setup, red, green, commit, finish\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 8: Attempt caps and escalation (retry, skip, abort)

**Files:**
- Modify: `src/phil/run/engine.py`
- Test: `tests/run/test_engine_escalation.py`

**Interfaces:**
- Consumes: Task 7 engine; `WorktreeManager.reset_to` (Task 5); `langgraph.types.interrupt`.
- Produces: `RunEngine.escalate` node; `route_after_escalate`. `_failed_attempt` escalates when `attempts >= config.run.max_attempts_per_phase` with `escalation = {"reason": "attempts", "task_id", "phase", "problems", "options": ["retry", "skip", "abort"], "summary": "<id> failed <n> attempts in the <phase> phase"}`. Resume values are dicts: `{"action": "retry", "hint": str | None}`, `{"action": "skip"}`, `{"action": "abort"}`. An action outside `escalation["options"]` raises `ValueError`. While paused the run row has `state="escalated"` and `needs_attention=summary`.

- [ ] **Step 1: Write the failing tests** — `tests/run/test_engine_escalation.py`:

```python
import pytest

from phil.config import PhilConfig
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import bad_green, write_green, write_red


def test_three_failed_greens_escalate(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    result = harness.start()
    escalation = result["__interrupt__"][0].value
    assert escalation["reason"] == "attempts"
    assert escalation["options"] == ["retry", "skip", "abort"]
    assert escalation["summary"] == "CALC-001 failed 3 attempts in the green phase"
    assert "tests still failing" in escalation["problems"][0]
    record = harness.run_record()
    assert (record.state, record.needs_attention) == ("escalated", escalation["summary"])


def test_retry_with_hint_reaches_the_implementer(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green, write_green]})
    harness.start()
    final = harness.resume({"action": "retry", "hint": "use a minus sign"})
    assert final["status"] == "completed"
    last_packet = [p for role, p in harness.factory.calls if role == "implementer"][-1]["messages"][0]["content"]
    assert "Human hint: use a minus sign" in last_packet
    assert harness.run_record().needs_attention is None


def test_skip_resets_the_worktree_and_marks_the_task(make_harness, calc_repo):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    harness.start()
    final = harness.resume({"action": "skip"})
    assert final["status"] == "completed"
    assert load_plan(final).tasks[0].status == "SKIPPED"
    assert not (harness.deps.worktree / "tests" / "test_sub.py").exists()
    assert "subtract" not in (harness.deps.worktree / "calc.py").read_text()
    assert run_git(calc_repo, "log", "--format=%s", "phil/r-0001").splitlines()[0] == "init"
    assert "(SKIPPED)" in (harness.deps.artifacts.run_dir / "summary.md").read_text()


def test_abort_finishes_as_aborted(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    harness.start()
    final = harness.resume({"action": "abort"})
    assert final["status"] == "aborted"
    assert harness.run_record().state == "aborted"


def test_rejected_output_counts_as_an_attempt(make_harness):
    harness = make_harness({"implementer": [{}, {}, write_red, write_green]})
    final = harness.start()
    assert final["status"] == "completed"
    outcomes = [row["outcome"] for row in harness.deps.conn.execute("SELECT outcome FROM telemetry ORDER BY id")]
    assert outcomes[:2] == ["invalid", "invalid"]


def test_attempt_cap_comes_from_config(make_harness):
    config = PhilConfig.model_validate({"run": {"max_attempts_per_phase": 1}})
    harness = make_harness({"implementer": [write_red, bad_green]}, config=config)
    result = harness.start()
    assert result["__interrupt__"][0].value["summary"] == "CALC-001 failed 1 attempts in the green phase"


def test_unknown_action_is_rejected(make_harness):
    harness = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    harness.start()
    with pytest.raises(ValueError, match="unknown escalation action"):
        harness.resume({"action": "approve"})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_engine_escalation.py -v`
Expected: FAIL (no interrupt is raised; bad greens loop until the script runs out).

- [ ] **Step 3: Implement** in `src/phil/run/engine.py`:

Add `from langgraph.types import interrupt` to the imports.

Replace `_failed_attempt`:

```python
    def _failed_attempt(self, state: RunState, problems: list[str], report: dict | None) -> dict:
        attempts = state.get("attempts", 0) + 1
        update = {"attempts": attempts, "last_problems": problems, "last_report": report, "verdict": "retry"}
        if attempts >= self.deps.config.run.max_attempts_per_phase:
            task = load_plan(state).tasks[state["task_index"]]
            update["verdict"] = "escalate"
            update["escalation"] = {
                "reason": "attempts",
                "task_id": task.id,
                "phase": state["phase"],
                "problems": problems,
                "options": ["retry", "skip", "abort"],
                "summary": f"{task.id} failed {attempts} attempts in the {state['phase']} phase",
            }
        return update
```

Add the node and router:

```python
    def escalate(self, state: RunState) -> dict:
        escalation = state["escalation"]
        self._update_run(state="escalated", current_node="escalate", needs_attention=escalation["summary"])
        decision = interrupt(escalation)
        action = decision.get("action")
        if action not in escalation["options"]:
            raise ValueError(f"unknown escalation action {action!r}; expected one of {escalation['options']}")
        self._update_run(state="running", needs_attention=None)
        cleared = {"escalation": None}
        if action == "retry":
            return {**cleared, "attempts": 0, "hint": decision.get("hint"), "next": "implement"}
        if action == "skip":
            self.worktrees.reset_to(self.deps.worktree, state["task_base_sha"])
            plan = with_task_status(load_plan(state), state["task_index"], "SKIPPED")
            return {**cleared, "plan": plan.model_dump(), "next": "pick_task"}
        return {**cleared, "status": "aborted", "next": "finish"}

    def route_after_escalate(self, state: RunState) -> str:
        return state["next"]
```

In `build`, add `graph.add_node("escalate", self.escalate)`; change the verify edge to `graph.add_conditional_edges("verify", self.route_after_verify, ["implement", "commit", "escalate"])`; add `graph.add_conditional_edges("escalate", self.route_after_escalate, ["implement", "pick_task", "finish"])`. In `route_after_verify` add `"escalate": "escalate"` to the mapping.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/run -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/engine.py tests/run/test_engine_escalation.py
git commit -m "$(printf 'Escalate after capped attempts with retry, skip, and abort\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 9: Shell approval after the call

**Files:**
- Modify: `src/phil/run/engine.py`
- Test: `tests/run/test_engine_approval.py`

**Interfaces:**
- Consumes: `CommandLog.denied`, `AgentContext.extra_allow` (Task 3); `escalate` (Task 8).
- Produces: when an implementer call used a denied command, `implement` sets `escalation = {"reason": "approval", "task_id", "commands": [...], "options": ["approve", "deny", "abort"], "summary": "<id> needs approval for: <commands>"}` and routes to `escalate`. `approve` adds the commands to `state["approved"]` (allowed for the rest of the run) and re-runs `implement` in the same phase without counting an attempt. `deny` records a hint (`"Not approved: <commands>. Do not use them."`) and continues to `verify`.

- [ ] **Step 1: Write the failing tests** — `tests/run/test_engine_approval.py`:

```python
import shlex
import sys

from tests.helpers import run_git
from tests.run.conftest import write_green, write_red

BUILD = f"{shlex.quote(sys.executable)} tools/build.py"


def add_build_script(repo):
    (repo / "tools").mkdir()
    (repo / "tools" / "build.py").write_text("print('built ok')\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "add build script")


def red_with_build(outputs):
    def script(turn):
        outputs.append(turn.tools["run_shell"](BUILD))
        return write_red(turn)

    return script


def test_denied_command_escalates_then_approval_allows_it(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness({"implementer": [red_with_build(outputs), red_with_build(outputs), write_green]})
    escalation = harness.start()["__interrupt__"][0].value
    assert escalation["reason"] == "approval"
    assert escalation["commands"] == [BUILD]
    assert outputs[0].startswith("DENIED:")

    final = harness.resume({"action": "approve"})
    assert final["status"] == "completed"
    assert outputs[1].startswith("exit_code: 0")
    assert "built ok" in outputs[1]
    assert final["approved"] == [BUILD]
    assert final["attempts"] == 0


def test_deny_continues_to_verify_with_a_hint(make_harness, calc_repo):
    add_build_script(calc_repo)
    outputs: list[str] = []
    harness = make_harness({"implementer": [red_with_build(outputs), write_green]})
    harness.start()
    final = harness.resume({"action": "deny"})
    assert final["status"] == "completed"
    green_packet = [p for role, p in harness.factory.calls if role == "implementer"][-1]["messages"][0]["content"]
    assert f"Not approved: {BUILD}. Do not use them." in green_packet
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_engine_approval.py -v`
Expected: FAIL (no approval escalation happens).

- [ ] **Step 3: Implement** in `src/phil/run/engine.py`:

At the end of `implement`, replace the `return` with:

```python
        update = {"call_seq": seq, "implement_failed": failed, "last_problems": problems, "denied": list(log.denied)}
        if log.denied:
            update["escalation"] = {
                "reason": "approval",
                "task_id": task.id,
                "commands": list(log.denied),
                "options": ["approve", "deny", "abort"],
                "summary": f"{task.id} needs approval for: {', '.join(log.denied)}",
            }
        return update

    def route_after_implement(self, state: RunState) -> str:
        return "escalate" if state.get("escalation") else "verify"
```

In `escalate`, before the final `return` (the abort branch), add:

```python
        if action == "approve":
            approved = [*state.get("approved", []), *escalation["commands"]]
            return {**cleared, "approved": approved, "denied": [], "next": "implement"}
        if action == "deny":
            hint = f"Not approved: {', '.join(escalation['commands'])}. Do not use them."
            return {**cleared, "denied": [], "hint": hint, "next": "verify"}
```

In `build`, replace `graph.add_edge("implement", "verify")` with `graph.add_conditional_edges("implement", self.route_after_implement, ["verify", "escalate"])`, and add `"verify"` to the `escalate` edge targets: `["implement", "verify", "pick_task", "finish"]`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/run -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/engine.py tests/run/test_engine_approval.py
git commit -m "$(printf 'Ask for approval of denied commands after the agent call\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 10: Tester pass with a product-code gate

**Files:**
- Modify: `src/phil/run/engine.py`, `src/phil/config.py`, `tests/test_config.py`
- Test: `tests/run/test_engine_tester.py`

**Interfaces:**
- Consumes: `TesterInput`, `TesterReport`, `Issue`, `issues_to_tasks`, `is_test_path`, `WorktreeManager.restore`, `PacketTooLarge`.
- Produces:
  - `PhilConfig.budget_for(role)` defaults: `architect` 24 000, `tester` 48 000, `reviewer` 48 000, others 12 000 (explicit `[budget.<role>]` still wins).
  - `tester` node (once per run, after all tasks, when `tester_done` is false) and, with `tester_mode = "task+run"`, a `tester_task` node after each commit of an original plan task. Both call `_run_tester(state, diff_base, node)`: runs the tester on the diff since `diff_base`; reverts any non-test file the tester changed (minor open issue `tester changed product files; reverted: ...`); commits remaining test changes as `<KEY>: tests from tester`; blocker/major issues become fix tasks via `issues_to_tasks(..., "tester")`; minor issues, denied commands, rejected output, and oversized packets become open issues.
  - `route_after_pick`: task → `implement`; none left and `tester_done` false → `tester`; otherwise `finish` (Task 11 changes this to `review`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`, change the assertion `assert config.budget_for("reviewer").max_input_tokens == 12000` to `assert config.budget_for("reviewer").max_input_tokens == 48000`, and append:

```python
def test_role_budget_defaults():
    config = load_config(Path("/nonexistent"))
    assert config.budget_for("implementer").max_input_tokens == 12000
    assert config.budget_for("architect").max_input_tokens == 24000
    assert config.budget_for("tester").max_input_tokens == 48000
```

(add `from pathlib import Path` to the imports if missing).

`tests/run/test_engine_tester.py`:

```python
from phil.config import PhilConfig
from phil.contracts import Issue, TesterReport
from phil.run.state import load_plan
from tests.helpers import run_git
from tests.run.conftest import self_check, task_result, tester_report, write_green, write_red


def write_edge_test(turn):
    (turn.workdir / "tests" / "test_edge.py").write_text(
        "from calc import subtract\n\n\ndef test_negative():\n    assert subtract(1, 3) == -2\n"
    )
    return TesterReport(tests_added=["tests/test_edge.py"], issues=[], self_check=self_check())


def red_multiply(turn):
    (turn.workdir / "tests" / "test_mul.py").write_text(
        "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
    )
    return task_result("red", ["tests/test_mul.py"], ["tests/test_mul.py"])


def green_multiply(turn):
    calc = turn.workdir / "calc.py"
    calc.write_text(calc.read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")
    return task_result("green", ["calc.py"])


def test_tester_tests_are_committed(make_harness, calc_repo):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [write_edge_test]})
    final = harness.start()
    assert final["status"] == "completed"
    assert final["tester_done"] is True
    subjects = run_git(calc_repo, "log", "--format=%s", "phil/r-0001").splitlines()
    assert subjects[0] == "CALC: tests from tester"
    assert (harness.deps.worktree / "tests" / "test_edge.py").exists()


def test_major_issue_becomes_a_fix_task(make_harness):
    issue = Issue(severity="major", note="add multiply(a, b)", file="calc.py")
    harness = make_harness({
        "implementer": [write_red, write_green, red_multiply, green_multiply],
        "tester": [tester_report([issue])],
    })
    final = harness.start()
    tasks = load_plan(final).tasks
    assert [(t.id, t.status) for t in tasks] == [("CALC-001", "DONE"), ("CALC-002", "DONE")]
    assert tasks[1].description == "Fix (tester): add multiply(a, b)"


def test_tester_product_changes_are_reverted(make_harness):
    def meddle(turn):
        (turn.workdir / "calc.py").write_text("broken = True\n")
        return tester_report([Issue(severity="minor", note="naming")])

    harness = make_harness({"implementer": [write_red, write_green], "tester": [meddle]})
    final = harness.start()
    assert "def subtract" in (harness.deps.worktree / "calc.py").read_text()
    notes = [issue["note"] for issue in final["open_issues"]]
    assert "naming" in notes
    assert "tester changed product files; reverted: calc.py" in notes


def test_task_plus_run_mode_audits_each_original_task(make_harness):
    config = PhilConfig.model_validate({"run": {"tester_mode": "task+run"}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report(), tester_report()]}, config=config
    )
    final = harness.start()
    assert final["status"] == "completed"
    assert [role for role, _ in harness.factory.calls].count("tester") == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_engine_tester.py tests/test_config.py -v`
Expected: FAIL (tester never runs; budget defaults are 12 000).

- [ ] **Step 3: Implement**

`src/phil/config.py`: add below `DEFAULT_MODEL`:

```python
DEFAULT_BUDGETS = {"architect": 24_000, "tester": 48_000, "reviewer": 48_000}
```

and change `budget_for`:

```python
    def budget_for(self, role: str) -> RoleBudget:
        default = RoleBudget(max_input_tokens=DEFAULT_BUDGETS.get(role, 12_000))
        return self.budget.get(role, default)
```

`src/phil/run/engine.py`: extend imports — `from phil.contracts import ImplementInput, Issue, TesterInput, TestReport`, `from phil.packets import PacketTooLarge, build_packet`, `from phil.run.gates import is_test_path, run_tests, snapshot_tests, verify_green, verify_red`, and `issues_to_tasks` from `phil.run.state`. Then add:

```python
    def _run_tester(self, state: RunState, diff_base: str, node: str) -> dict:
        plan = load_plan(state)
        worktree = self.deps.worktree
        globs = self.deps.config.project.test_globs
        seq = state.get("call_seq", 0) + 1
        self._update_run(current_node=node)
        head_before = self.worktrees.head(worktree)
        before = self._test(state, artifact_name(node, None, seq))
        notes: list[Issue] = []
        issues: list[Issue] = []
        log = CommandLog()
        contract = TesterInput(
            plan=plan, diff=self.worktrees.diff(worktree, diff_base), final_report=before, test_cmd=state["test_cmd"]
        )
        try:
            packet = build_packet("tester", contract, budget_tokens=self._budget("tester"))
            report = invoke_agent(get_spec("tester"), packet, self._context(state, log), node=node, call=seq)
            issues = list(report.issues)
        except PacketTooLarge as exc:
            notes.append(Issue(severity="minor", note=f"tester skipped: {exc}"))
        except ContractViolation as exc:
            notes.append(Issue(severity="minor", note=f"tester output rejected: {'; '.join(exc.problems)}"))
        product = [p for p in self.worktrees.changed_files(worktree, since=head_before) if not is_test_path(p, globs)]
        if product:
            self.worktrees.restore(worktree, product)
            notes.append(Issue(severity="minor", note=f"tester changed product files; reverted: {', '.join(product)}"))
        if self.worktrees.changed_files(worktree, since=head_before):
            self.worktrees.commit_all(worktree, f"{plan.keyword}: tests from tester")
        notes += [Issue(severity="minor", note=f"tester command not approved: {cmd}") for cmd in log.denied]
        blocking = [issue for issue in issues if issue.severity in ("blocker", "major")]
        minor = [issue for issue in issues if issue.severity == "minor"]
        plan = issues_to_tasks(plan, blocking, "tester") if blocking else plan
        open_issues = [*state.get("open_issues", []), *(issue.model_dump() for issue in [*minor, *notes])]
        return {"plan": plan.model_dump(), "call_seq": seq, "open_issues": open_issues}

    def tester(self, state: RunState) -> dict:
        return {**self._run_tester(state, state["base_sha"], "tester"), "tester_done": True}

    def tester_task(self, state: RunState) -> dict:
        return self._run_tester(state, state["task_base_sha"], "tester_task")

    def route_after_commit(self, state: RunState) -> str:
        task = load_plan(state).tasks[state["task_index"]]
        audit = self.deps.config.run.tester_mode == "task+run" and task.id in state.get("original_task_ids", [])
        return "tester_task" if audit else "pick_task"
```

Replace `route_after_pick`:

```python
    def route_after_pick(self, state: RunState) -> str:
        if state["task_index"] >= 0:
            return "implement"
        return "finish" if state.get("tester_done") else "tester"
```

In `build`: add nodes `graph.add_node("tester", self.tester)` and `graph.add_node("tester_task", self.tester_task)`; change the pick edge targets to `["implement", "tester", "finish"]`; replace `graph.add_edge("commit", "pick_task")` with `graph.add_conditional_edges("commit", self.route_after_commit, ["tester_task", "pick_task"])`; add `graph.add_edge("tester", "pick_task")` and `graph.add_edge("tester_task", "pick_task")`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all PASS once earlier tests are updated. Earlier engine tests that run to the end now also reach the tester, so add `"tester": [tester_report()]` (imported from `tests.run.conftest`) to the scripts in `tests/run/test_engine_happy.py`, the finishing tests in `test_engine_escalation.py` (retry, skip, rejected-output, and attempt-cap tests that later retry; abort needs none), and `test_engine_approval.py`. The happy-path `remaining()` assertion becomes `{"implementer": 0, "tester": 0}`; its first log subject stays `CALC-001: Add subtract` because an empty tester report adds no commit.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/engine.py src/phil/config.py tests/test_config.py tests/run
git commit -m "$(printf 'Add tester pass with a product-code gate and fix tasks\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 11: Reviewer rounds

**Files:**
- Modify: `src/phil/run/engine.py`
- Test: `tests/run/test_engine_review.py`

**Interfaces:**
- Consumes: `ReviewInput`, `Review`, `issues_to_tasks`, `ArtifactStore.read_assumptions`.
- Produces: `review` node after all tasks and the tester pass. It runs the tests, sends `ReviewInput(plan, diff since base, final_report, open_assumptions)` to the reviewer, and increments `review_rounds`. If the verdict is `changes`, there are blocker/major issues, and `review_rounds < config.run.max_review_rounds`, those issues become fix tasks (`"review"`) and the run returns to `pick_task`; otherwise it goes to `finish`, carrying all remaining issues into `open_issues`. `route_after_pick` now sends "no tasks left, tester done" to `review`.

- [ ] **Step 1: Write the failing tests** — `tests/run/test_engine_review.py`:

```python
from phil.contracts import Issue
from phil.run.state import load_plan
from tests.run.conftest import review, self_check, tester_report, write_green, write_red
from tests.run.test_engine_tester import green_multiply, red_multiply


def test_approval_finishes_the_run(make_harness):
    harness = make_harness({"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]})
    final = harness.start()
    assert (final["status"], final["review_rounds"]) == ("completed", 1)


def test_changes_create_fix_tasks_then_second_review(make_harness):
    issue = Issue(severity="major", note="add multiply(a, b)")
    harness = make_harness({
        "implementer": [write_red, write_green, red_multiply, green_multiply],
        "tester": [tester_report()],
        "reviewer": [review("changes", [issue]), review()],
    })
    final = harness.start()
    assert final["review_rounds"] == 2
    assert [t.id for t in load_plan(final).tasks] == ["CALC-001", "CALC-002"]
    assert final["status"] == "completed"


def test_review_round_cap_leaves_issues_open(make_harness):
    first = Issue(severity="major", note="add multiply(a, b)")
    second = Issue(severity="major", note="docstrings missing", file="calc.py")
    harness = make_harness({
        "implementer": [write_red, write_green, red_multiply, green_multiply],
        "tester": [tester_report()],
        "reviewer": [review("changes", [first]), review("changes", [second])],
    })
    final = harness.start()
    assert final["review_rounds"] == 2
    assert [issue["note"] for issue in final["open_issues"]] == ["docstrings missing"]
    assert "- (major) docstrings missing [calc.py]" in (harness.deps.artifacts.run_dir / "summary.md").read_text()


def test_reviewer_sees_open_assumptions(make_harness):
    def red_with_assumption(turn):
        result = write_red(turn)
        return result.model_copy(update={"self_check": self_check().model_copy(update={"assumptions": ["ints only"]})})

    harness = make_harness({
        "implementer": [red_with_assumption, write_green], "tester": [tester_report()], "reviewer": [review()],
    })
    harness.start()
    review_packet = [p for role, p in harness.factory.calls if role == "reviewer"][0]["messages"][0]["content"]
    assert "ints only" in review_packet
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_engine_review.py -v`
Expected: FAIL (the reviewer is never called).

- [ ] **Step 3: Implement** in `src/phil/run/engine.py` (add `ReviewInput` to the contracts import):

```python
    def review(self, state: RunState) -> dict:
        plan = load_plan(state)
        worktree = self.deps.worktree
        seq = state.get("call_seq", 0) + 1
        rounds = state.get("review_rounds", 0) + 1
        self._update_run(current_node="review")
        final = self._test(state, artifact_name("review", None, seq))
        assumptions = [e["assumption"] for e in self.deps.artifacts.read_assumptions() if e.get("status") == "open"]
        contract = ReviewInput(
            plan=plan, diff=self.worktrees.diff(worktree, state["base_sha"]), final_report=final,
            open_assumptions=assumptions,
        )
        carried = list(state.get("open_issues", []))
        try:
            packet = build_packet("reviewer", contract, budget_tokens=self._budget("reviewer"))
            verdict = invoke_agent(get_spec("reviewer"), packet, self._context(state, CommandLog()), node="review", call=seq)
        except (PacketTooLarge, ContractViolation) as exc:
            note = Issue(severity="minor", note=f"review not completed: {exc}")
            return {"call_seq": seq, "review_rounds": rounds, "open_issues": [*carried, note.model_dump()], "next": "finish"}
        blocking = [issue for issue in verdict.issues if issue.severity in ("blocker", "major")]
        minor = [issue for issue in verdict.issues if issue.severity == "minor"]
        if verdict.verdict == "changes" and blocking and rounds < self.deps.config.run.max_review_rounds:
            plan = issues_to_tasks(plan, blocking, "review")
            return {
                "plan": plan.model_dump(), "call_seq": seq, "review_rounds": rounds,
                "open_issues": [*carried, *(issue.model_dump() for issue in minor)], "next": "pick_task",
            }
        return {
            "call_seq": seq, "review_rounds": rounds,
            "open_issues": [*carried, *(issue.model_dump() for issue in verdict.issues)], "next": "finish",
        }

    def route_after_review(self, state: RunState) -> str:
        return state["next"]
```

Replace `route_after_pick`:

```python
    def route_after_pick(self, state: RunState) -> str:
        if state["task_index"] >= 0:
            return "implement"
        return "review" if state.get("tester_done") else "tester"
```

In `build`: add `graph.add_node("review", self.review)`; change the pick edge targets to `["implement", "tester", "review"]`; add `graph.add_conditional_edges("review", self.route_after_review, ["pick_task", "finish"])`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all PASS once every earlier harness that reaches the end also has `"reviewer": [review()]` (imported from `tests.run.conftest`). The happy-path `remaining()` assertion becomes `{"implementer": 0, "tester": 0, "reviewer": 0}`.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/engine.py tests/run
git commit -m "$(printf 'Add reviewer rounds that turn issues into fix tasks\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

### Task 12: Budget guard and the runner (start, resume, continue after a crash)

**Files:**
- Modify: `src/phil/run/engine.py`
- Create: `src/phil/run/runner.py`
- Test: `tests/run/test_runner.py`

**Interfaces:**
- Consumes: `run_totals` (plan 1), the full engine.
- Produces:
  - Budget guard: before each agent call in `implement`, `_run_tester`, and `review`, if `budget_override` is false and the run's telemetry totals reach `config.run.max_tokens` or `max_cost_usd`, the node returns only `{"escalation": {"reason": "budget", "options": ["continue", "abort"], "resume_to": <node>, "summary": "run used <t> tokens ($<c>); limit <max> tokens / $<max_cost>"}}` and routes to `escalate`. `continue` sets `budget_override=True` and returns to `resume_to`.
  - `phil.run.runner`: frozen dataclass `RunOutcome(status: str, escalation: dict | None = None)`; `thread_config(run_id) -> dict`; `start(engine, graph, *, plan, base_sha, test_cmd) -> RunOutcome`; `resume(engine, graph, decision: dict) -> RunOutcome`; `continue_run(engine, graph) -> RunOutcome` (re-drives from the last checkpoint after a crash). Status is `"escalated"` while paused, otherwise the final `status`.

- [ ] **Step 1: Write the failing tests** — `tests/run/test_runner.py`:

```python
import pytest

from phil.config import PhilConfig
from phil.run import runner
from tests.run.conftest import bad_green, calc_plan, review, tester_report, write_green, write_red, TEST_CMD


def outcome_start(harness):
    return runner.start(harness.engine, harness.graph, plan=harness.plan, base_sha=harness.base_sha, test_cmd=TEST_CMD)


def test_start_and_resume_across_fresh_engines(make_harness):
    first = make_harness({"implementer": [write_red, bad_green, bad_green, bad_green]})
    paused = outcome_start(first)
    assert paused.status == "escalated"
    assert paused.escalation["reason"] == "attempts"

    second = make_harness({"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]})
    done = runner.resume(second.engine, second.graph, {"action": "retry"})
    assert done == runner.RunOutcome(status="completed")


def test_continue_after_a_crash(make_harness):
    crashing = make_harness({"implementer": [write_red, RuntimeError("worker died")]})
    with pytest.raises(RuntimeError, match="worker died"):
        outcome_start(crashing)

    recovered = make_harness({"implementer": [write_green], "tester": [tester_report()], "reviewer": [review()]})
    assert runner.continue_run(recovered.engine, recovered.graph).status == "completed"


def test_budget_limit_escalates_and_can_continue(make_harness):
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness(
        {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]},
        config=config, usage=(100, 20, 0.0),
    )
    paused = outcome_start(harness)
    assert paused.escalation["reason"] == "budget"
    assert paused.escalation["resume_to"] == "implement"
    assert paused.escalation["summary"] == "run used 120 tokens ($0.00); limit 100 tokens / $2.00"
    assert runner.resume(harness.engine, harness.graph, {"action": "continue"}).status == "completed"


def test_budget_abort(make_harness):
    config = PhilConfig.model_validate({"run": {"max_tokens": 100}})
    harness = make_harness({"implementer": [write_red]}, config=config, usage=(100, 20, 0.0))
    outcome_start(harness)
    assert runner.resume(harness.engine, harness.graph, {"action": "abort"}).status == "aborted"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_runner.py -v`
Expected: FAIL with `ImportError: cannot import name 'runner'` (module missing).

- [ ] **Step 3: Implement**

`src/phil/run/engine.py` — add `from phil.store.telemetry import run_totals` and:

```python
    def _budget_escalation(self, state: RunState, node: str) -> dict | None:
        if state.get("budget_override"):
            return None
        tokens, cost = run_totals(self.deps.conn, self.deps.run_id)
        limits = self.deps.config.run
        if tokens < limits.max_tokens and cost < limits.max_cost_usd:
            return None
        return {
            "reason": "budget",
            "options": ["continue", "abort"],
            "resume_to": node,
            "summary": (
                f"run used {tokens} tokens (${cost:.2f}); "
                f"limit {limits.max_tokens} tokens / ${limits.max_cost_usd:.2f}"
            ),
        }
```

At the top of `implement`: `if (escalation := self._budget_escalation(state, "implement")) is not None: return {"escalation": escalation}`. Do the same at the top of `tester` (with `"tester"`), `tester_task` (with `"tester_task"`), and `review` (with `"review"`). Add routers so those nodes can reach `escalate`:

```python
    def route_after_tester(self, state: RunState) -> str:
        return "escalate" if state.get("escalation") else "pick_task"
```

and make `route_after_review` return `"escalate"` when `state.get("escalation")` is set, otherwise `state["next"]`. In `build`: replace `graph.add_edge("tester", "pick_task")` and `graph.add_edge("tester_task", "pick_task")` with `graph.add_conditional_edges("tester", self.route_after_tester, ["pick_task", "escalate"])` and the same for `tester_task`; add `"escalate"` to the review edge targets; extend the escalate edge targets to `["implement", "verify", "pick_task", "finish", "tester", "tester_task", "review"]`. In `escalate`, add before the final abort `return`:

```python
        if action == "continue":
            return {**cleared, "budget_override": True, "next": escalation["resume_to"]}
```

`src/phil/run/runner.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/run/engine.py src/phil/run/runner.py tests/run/test_runner.py
git commit -m "$(printf 'Add budget guard and runner with resume and crash recovery\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
```

---

## Spec coverage for this plan

| Spec section / follow-up | Covered here | Deferred to |
|---|---|---|
| §7 nodes: setup, pick_task, implement, verify_red/green, commit, escalate, tester, review, finish | Tasks 7–11 | — |
| §7 TDD enforced by gates; red may fail on missing symbols; test files frozen after red | Tasks 4, 5, 7 | — |
| §7 caps: 3 attempts per phase, 2 review rounds, 1 tester round | Tasks 8, 10, 11 | — |
| §7 tester_mode `run` and `task+run` | Task 10 | — |
| §8 tester different model family | config per role (plan 1) | Choose models in plan 4 |
| §8 reviewer resolves open assumptions | Task 11 (sent to reviewer) | Assumption ids: plan 4 |
| §10 escalation, shell approval (after the call), budget exceeded, abort keeps worktree | Tasks 8, 9, 12 | — |
| §10 worker crash → resume from checkpoint | Task 12 (`continue_run`) | Heartbeat and detection: 3b |
| §4.4 checkpoints in `phil.db` | Task 1 | — |
| Follow-up: tester product-code gate | Task 10 | — |
| Follow-up: budgets for tester/reviewer | Task 10 | Diff as a file reference if 48k is still too small |
| Follow-up: per-role fakes | Task 2 | — |
| Follow-up: empty commit | Task 7 (commit only when files changed) | — |
| Follow-up: `test_globs` semantics (path or name) | Task 4 | — |
| Follow-up: validate `Plan.test_cmd` against the allowlist | not needed (gates run it directly) | Plan 4 shows it at approval |
| Follow-up: usage callback, SDK timeout, 200-with-error transient, lean architect | — | Plan 3b / 4 |
| Detached worker, heartbeat, `attach` / `resume` / `stop` / `diff` / `clean` CLI | — | Plan 3b |
