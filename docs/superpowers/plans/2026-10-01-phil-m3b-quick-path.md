# Phil M3b: Quick Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A quick-routed change runs as a single task, planned by intake and run by a light implementer, with no tester and one review whose findings are fixed within the same task. A failed quick run can be moved up to the full pipeline.

**Architecture:**
- The run learns its `depth` (a new `runs.depth` column, then `RunState.depth`).
- The engine branches on that depth at five points:
  - skip the tester;
  - cap the attempts;
  - use a light implementer;
  - patch review findings within the task;
  - add a `full` escalation option.
- Intake writes the single task, which goes into `Goal.task`, and the chat builds a one-task plan from it and starts the run without an approval prompt.
- Choosing `full` ends the quick run, saves its worklogs as a handoff artifact, and starts the normal architect, critic and approval flow with that handoff as `prior_attempt`.

**Tech stack:** Python 3.14, uv, pydantic, LangGraph, LangChain 1.4 (`create_agent`), deepagents 0.7.19 (`FilesystemMiddleware`, `FilesystemBackend`, `FilesystemPermission`), SQLite migrations, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-phil-m3-proportional-orchestration-design.md`, §4 (revised 2026-10-01) and §5.2.

## Global Constraints

- **Keys:** never print or log key values; keys are read through `phil.key_store.key_lookup()`; agent-run commands keep the null keyring backend (`child_env`).
- **Import rule:** `phil.routing.*`, `phil.cli.main`, `phil.chat.*` and `phil.config` must not import langchain, langgraph or deepagents at module level.
- **Depth values:** `"quick"` or `"full"` on runs. Runs created before the migration have `NULL`, which is treated as `"full"`.
- **Quick-run settings:**
  - `run.quick_max_attempts = 2`;
  - one fix attempt after review;
  - the light implementer's `max_model_calls = 15` per attempt, with the existing hard stop 2 calls later (`BUDGET_GRACE_CALLS`).
- **Writes:** the light implementer can never write `.git/**` or `phil.toml`. This is the same rule as `filesystem_permissions(spec)`. Eviction of large tool results stays off in the light harness.
- **Escalation options:** a quick run started from a chat (`runs.chat_id` set) gets `["full", "retry", "abort"]`; a quick run without a chat gets `["retry", "abort"]`. Full runs are unchanged.
- **Exact user-facing strings:**
  - status line: `{label} · quick path  (/full to plan it properly)`;
  - forced quick: `Forced: quick path`;
  - the fix offer: `Fix · quick path`;
  - the fix prompt: `Fix it? [Enter = quick fix / full = plan it / n]`;
  - when the quick plan falls back: `Couldn't plan this as a quick change; planning it fully.`;
  - the quick-start line: `Quick change: {task.id} {task.description}`;
  - the move-to-full line: `Moving this to a full plan, with what the quick attempt learned.`
- **Tests** never touch the network or the real keychain. Live and bench tests are opt-in, and the user runs them.
- **Commits:** every message ends with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Write the message with the Write tool and commit with `git commit -F`. Keep the words "keychain" and "credentials" out of Bash command lines. Edit files only with the Edit or Write tools.
- **Never work around a hook, guard or permission denial.** Stop and report BLOCKED.

## Review Focus

1. **A quick plan whose task uses an unsupported `check_cmd`.** `launch_problems` refuses it. Expected: fall back to the full path with the fallback line, rather than starting a run that can't pass. Tested in Task 5.
2. **A review finding that would need a test change.** The patch is a check-mode task, so test files can't change. Expected: the fix attempt still runs, and if the gate passes the finding is listed as "fixed after review (unverified)" rather than silently dropped. Tested in Task 3.
3. **`full` chosen on a quick run whose chat has since been closed.** The `full` option only appears for chat runs. Answering `full` from `phil resume` in a terminal must fail cleanly: `full is only available in the chat that started this run; answer retry or abort.` Tested in Task 6.
4. **A pre-migration run, with depth `NULL`, resumed after upgrading.** It must behave as a full run. Tested in Task 1.
5. **Intake returns `depth="quick"` with `open_questions`.** Expected: the questions are asked first, and the quick plan is built only once there are no open questions. Tested in Task 5.

---

## File structure

| File | Change |
|---|---|
| `src/phil/store/db.py`, `src/phil/store/runs.py` | `runs.depth` migration, `RunRecord.depth`, `create_run(depth=)` |
| `src/phil/run/launch.py` | `prepare_run(..., depth=)` |
| `src/phil/run/state.py` | `RunState.depth`, `initial_state(..., depth=)`, `patching`, `patched_issues` |
| `src/phil/run/runner.py`, `src/phil/run/worker.py` | pass `record.depth` into `start` |
| `src/phil/config.py` | `RunConfig.quick_max_attempts` |
| `src/phil/run/engine.py` | the quick branches (tester, attempts, review patch, `full` option, implementer spec) |
| `src/phil/ui/show_view.py` | show depth |
| `src/phil/agents/spec.py`, `factory.py`, `registry.py` | light harness with writes; `prompt_name`; the `quick_implementer` spec |
| `src/phil/contracts/interface.py`, `inputs.py`, `routing.py` | `Goal.task`; `IntakeInput.route_depth` and `detected_test_cmd`; `ArchitectInput.prior_attempt`; `Answer.diagnosis` |
| `src/phil/prompts/intake.md`, `architect.md`, `answer.md` | prompt lines |
| `src/phil/chat/planning.py` | `intake(..., route_depth=, detected_test_cmd=)`; `quick_plan(goal, test_cmd)`; `Planner.draft(..., prior_attempt=)` |
| `src/phil/chat/controller.py` | the quick flow, status lines, the fix prompt, moving to full |
| `tests/live/bench/cases.py`, `harness.py`, `report.py` | router assertion, quick and answer paths, call counts |
| `README.md`, `docs/superpowers/roadmap.md`, `docs/superpowers/plans/2026-10-01-phil-m3b-followups.md` | docs |

---

### Task 1: The run records its depth

**Files:**
- Modify: `src/phil/store/db.py` (append to `MIGRATIONS`)
- Modify: `src/phil/store/runs.py` (`RunRecord`, `create_run`)
- Modify: `src/phil/run/launch.py` (`prepare_run`)
- Modify: `src/phil/run/state.py` (`RunState`, `initial_state`)
- Modify: `src/phil/run/runner.py` (`start`)
- Modify: `src/phil/run/worker.py` (pass the depth)
- Modify: `src/phil/config.py` (`RunConfig.quick_max_attempts`)
- Modify: `src/phil/ui/show_view.py`
- Test: `tests/store/test_runs.py`, `tests/run/test_launch.py`, `tests/run/test_state.py` (or wherever `initial_state` is tested; search for it), `tests/ui/test_show_view.py` (or the show tests; search for `render_show`)

**Interfaces:**
- Produces:
  - `RunRecord.depth: str | None = None`;
  - `create_run(..., depth: str | None = None)`;
  - `prepare_run(info, plan, base_sha, *, chat_id=None, overrides=(), depth: str = "full")`;
  - `RunState.depth: str`;
  - `initial_state(run_id, plan, base_sha, test_cmd, config_test_cmd=None, depth: str = "full")`;
  - `runner.start(..., depth: str = "full")`;
  - `RunConfig.quick_max_attempts: int = 2`, validated `>= 1`;
  - `phil.run.state.run_depth(state) -> str`, which returns `state.get("depth") or "full"`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/store/test_runs.py
def test_create_run_records_depth(tmp_path):
    conn = connect(tmp_path / "phil.db")
    record = create_run(conn, run_id="r-0001", keyword="FIX", base_sha="a" * 40, worktree=tmp_path / "wt",
                        tasks_total=1, depth="quick")
    assert record.depth == "quick"
    assert get_run(conn, "r-0001").depth == "quick"


def test_a_run_without_depth_reads_as_none(tmp_path):
    conn = connect(tmp_path / "phil.db")
    record = create_run(conn, run_id="r-0002", keyword="FIX", base_sha="a" * 40, worktree=tmp_path / "wt",
                        tasks_total=1)
    assert record.depth is None
```

```python
# state tests
from phil.run.state import initial_state, run_depth


def test_initial_state_carries_depth_and_quick_skips_the_tester(sample_plan):
    quick = initial_state("r-1", sample_plan, "a" * 40, "pytest", depth="quick")
    assert quick["depth"] == "quick" and quick["tester_done"] is True
    full = initial_state("r-1", sample_plan, "a" * 40, "pytest")
    assert full["depth"] == "full" and full["tester_done"] is False


def test_run_depth_treats_missing_as_full():
    assert run_depth({}) == "full"
    assert run_depth({"depth": None}) == "full"
    assert run_depth({"depth": "quick"}) == "quick"
```

Use the existing plan fixture from the run tests, or build a one-task `Plan` inline.

Also add:
- a `prepare_run(..., depth="quick")` test in `tests/run/test_launch.py` asserting `record.depth == "quick"`;
- a worker test asserting a quick record starts a run whose state has `depth == "quick"`. Follow the existing worker tests (`tests/run/test_worker*.py`) and their fake factory;
- a show-view test asserting the header contains `· quick ·` for a quick run, and no depth text for a `NULL`-depth run;
- `PhilConfig.model_validate({"run": {"quick_max_attempts": 0}})` raises.

- [ ] **Step 2: Run them and confirm they fail.**

Run: `uv run pytest tests/store tests/run tests/ui -q -n 0`
Expected: FAIL (unknown `depth`).

- [ ] **Step 3: Implement.**
  - `db.py`: append `"ALTER TABLE runs ADD COLUMN depth TEXT",  # "quick" or "full"; NULL for runs before M3b` to `MIGRATIONS`. Never edit earlier entries.
  - `runs.py`:
    - add `depth: str | None = None` as the last `RunRecord` field;
    - add a `depth` parameter to `create_run`, and add the column to the INSERT;
    - `depth` is immutable, so leave it out of `_UPDATABLE`.
  - `launch.py`: add `depth: str = "full"` to `prepare_run` and pass it to `create_run`.
  - `state.py`:
    - add `depth: str`, `patching: bool` and `patched_issues: list[dict[str, Any]]` to `RunState`;
    - `initial_state(..., depth="full")` sets `depth=depth`, `tester_done=(depth == "quick")`, `patching=False` and `patched_issues=[]`;
    - add `def run_depth(state) -> str: return state.get("depth") or "full"`.
  - `runner.py`: `start(..., depth: str = "full")` passes it to `initial_state`.
  - `worker.py`: the start path passes `depth=record.depth or "full"`.
  - `config.py`: add `quick_max_attempts: int = 2` to `RunConfig`, with a validator that rejects values below 1.
  - `show_view.py`: in `render_show`'s header, insert `· {record.depth} ·` after the keyword when `record.depth` is set.
- [ ] **Step 4: Run the focused tests, then the full suite once.**
- [ ] **Step 5: Commit.** Message: `Record each run's depth`, plus the trailer.

---

### Task 2: The light harness can write; add the quick implementer spec

**Files:**
- Modify: `src/phil/agents/spec.py` (`prompt_name`)
- Modify: `src/phil/agents/factory.py` (`_build_light_agent`)
- Modify: `src/phil/agents/registry.py` (`quick_implementer`)
- Test: `tests/agents/test_light_harness.py`, `tests/agents/test_registry.py`

**Interfaces:**
- Produces:
  - `AgentSpec.prompt_name: str | None = None`. When set, `load_prompt` reads `f"{prompt_name}.md"` instead of `f"{name}.md"`;
  - `SPECS["quick_implementer"] = AgentSpec("quick_implementer", "implementer", ImplementInput, TaskResult, tools=("shell",), writes_files=True, harness="light", max_model_calls=QUICK_IMPLEMENTER_MAX_MODEL_CALLS, prompt_name="implementer")`;
  - `QUICK_IMPLEMENTER_MAX_MODEL_CALLS = 15` in `registry.py`.

- [ ] **Step 1: Write the failing tests**, in `tests/agents/test_light_harness.py`. Follow its existing helpers: the fake chat model and capturing `create_agent` kwargs.

```python
def test_quick_implementer_spec():
    spec = get_spec("quick_implementer")
    assert (spec.harness, spec.role, spec.writes_files, spec.max_model_calls) == ("light", "implementer", True, 15)
    assert load_prompt(spec) == load_prompt(get_spec("implementer"))  # same prompt text


def test_a_writing_light_agent_gets_write_tools_with_git_and_phil_toml_denied(tmp_path, monkeypatch):
    # Build the quick implementer over tmp_path with a fake model, then drive its write_file tool:
    # writing "/notes.txt" succeeds; writing "/.git/config" and "/phil.toml" is refused, and neither
    # file changes on disk.
    ...


def test_a_writing_light_agent_still_writes_nothing_on_its_own(tmp_path, monkeypatch):
    # Large tool results are not evicted into the repo (no /large_tool_results), as for the answerer.
    ...
```

Write the bodies in full, modelled on the existing `test_a_light_agent_writes_nothing_into_the_repo` and the scripted-model tests in the same file. Drive the write tool through scripted tool calls: the scripted model emits a `write_file` call, then the structured answer. Assert on the files in `tmp_path` and on the tool result text that reports a denial.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
  - `spec.py`: add `prompt_name: str | None = None` with a comment. In `load_prompt`, use `spec.prompt_name or spec.name` for the file name.
  - `factory.py` `_build_light_agent`:
    - remove the `writes_files` refusal;
    - build `FilesystemMiddleware(backend=..., tools=list(READ_TOOLS) if not spec.writes_files else "all", tool_token_limit_before_evict=None, human_message_token_limit_before_evict=None, _permissions=filesystem_permissions(spec))`;
    - check the installed signature (`.venv/lib/python3.14/site-packages/deepagents/middleware/filesystem.py`, around line 1721) and keep the eviction limits as they are today;
    - update the comment that mentioned M3b.
  - `registry.py`: add `QUICK_IMPLEMENTER_MAX_MODEL_CALLS = 15` and the spec. Extend the registry enumeration tests additively: "quick_implementer" joins the shell users and the light specs.
- [ ] **Step 4: Run the focused tests, then the full suite once.**
- [ ] **Step 5: Commit.** Message: `Let the light harness write, and add the quick implementer`, plus the trailer.

---

### Task 3: Quick runs in the engine

**Files:**
- Modify: `src/phil/run/engine.py`
- Test: `tests/run/test_engine_quick.py` (new), plus the existing engine tests (unchanged behaviour for full runs)

**Interfaces:**
- Consumes: `run_depth`, the `RunState` fields `depth`, `patching` and `patched_issues`, `RunConfig.quick_max_attempts` (Task 1), and `SPECS["quick_implementer"]` (Task 2).
- Produces: the escalation action `"full"`, which the engine handles like abort but records `next="finish"`, `status="aborted"` and `moved_to_full=True` in state. It also writes the handoff artifact `handoff/prior_attempt.json` with `{"worklogs": [<AttemptWorklog dicts in task order>], "open_issues": [...]}` through `self.deps.artifacts.write_json("handoff", "prior_attempt", payload)`.

Behaviour (spec §4.2 and §4.3). For a quick run, where `run_depth(state) == "quick"`:
1. **The tester is never visited.** `tester_done` starts true (Task 1). `route_after_commit` returns `"pick_task"` (never `"tester_task"`).
2. **The implementer:** `implement()` uses `get_spec("quick_implementer")` instead of `get_spec("implementer")`.
3. **Attempts:**
   - `_failed_attempt`'s limit is `config.run.quick_max_attempts` for quick runs, and `1` while `state["patching"]` is true;
   - the escalation `options` are `["full", "retry", "abort"]` if this run has a chat (`get_run(self.deps.conn, self.deps.run_id).chat_id` is set), else `["retry", "abort"]`;
   - the summary while patching: `f"{task.id}'s fix after review didn't pass the gate"`.
4. **Review:**
   - **First review**, with `review_rounds` still 0 before the increment: if `verdict.verdict == "changes"` and there are blocking (blocker or major) issues, don't call `issues_to_tasks`. Instead:
     - reopen the reviewed task, which is the last task; quick plans have exactly one;
     - set its `status` to `"TODO"`, `verify` to `"check"`, and `check_cmd` to `task.check_cmd or state["test_cmd"]`;
     - add each blocking issue's note to its `acceptance_criteria` as `f"Review: {note}"`;
     - set `patching=True` and `patched_issues=[blocking dicts]`;
     - put `minor` into `open_issues`;
     - set `next="pick_task"`.

     `pick_task` then picks it as a check task (green phase).
   - **Any later review** in a quick run, when `review_rounds >= 1`: no reviewer call. Return `next="finish"`, and add each patched issue to `open_issues` as `{"severity": "minor", "note": f"fixed after review (unverified): {note}", ...}`.
   - Without blocking issues, the first review finishes as today.
5. **The `full` escalation action:** write the handoff artifact, then return `{**cleared, "status": "aborted", "moved_to_full": True, "next": "finish"}`. Add `moved_to_full: bool` to `RunState` in this task.

Full runs (depth `full` or `NULL`) must behave exactly as before. Run the existing engine tests unchanged to prove it.

- [ ] **Step 1: Write the failing tests** in `tests/run/test_engine_quick.py`, using the existing engine test harness (read `tests/run/conftest.py` and an existing engine test such as `tests/run/test_engine*.py` for the fake factory, worktree fixtures and how scripted `TaskResult`/`Review` outputs are fed). The tests:
   - `test_quick_run_never_calls_the_tester`: a one-task quick plan, the implementer succeeds, the review says ok. The factory saw no `tester` spec, and the run completed.
   - `test_quick_run_uses_the_quick_implementer`: `factory.built` contains `("quick_implementer", …)` and not `("implementer", …)`.
   - `test_quick_attempts_cap_at_two_and_offer_full_in_a_chat_run`: a run created with a `chat_id` whose gate fails twice escalates with `options == ["full", "retry", "abort"]`.
   - `test_quick_run_without_a_chat_offers_retry_and_abort`.
   - `test_review_findings_are_patched_in_the_same_task`:
     - the review returns `changes` with one major issue; no new task is appended (`len(plan.tasks) == 1`);
     - the task is re-run as a check task with `Review: <note>` in its criteria; the reviewer is called exactly once;
     - the run completes with `open_issues` containing `fixed after review (unverified): <note>`.
   - `test_a_failed_fix_after_review_escalates_after_one_attempt`: the summary mentions the fix after review, and the options include `full` for a chat run.
   - `test_full_action_aborts_and_writes_the_handoff`: resume with `{"action": "full"}`. The run state is aborted, `moved_to_full` is true, and `handoff/prior_attempt.json` exists with the task's worklog.
   - `test_full_runs_unchanged`: a full-depth run with the same review still appends a fix task via `issues_to_tasks` and still runs the tester.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement** the behaviour above, in small helpers on `RunEngine`: `_is_quick(state)`, `_attempt_limit(state)`, `_escalation_options(state)`, `_patch_after_review(state, plan, blocking, minor, ...)` and `_write_handoff(state)`. Keep the existing nodes' shape.
- [ ] **Step 4: Run tests/run, then the full suite once.**
- [ ] **Step 5: Commit.** Message: `Run quick-depth runs lighter: no tester, two attempts, fixes within the task`, plus the trailer.

---

### Task 4: Contracts and planning helpers

**Files:**
- Modify: `src/phil/contracts/interface.py` (`Goal.task`)
- Modify: `src/phil/contracts/inputs.py` (`IntakeInput`, `ArchitectInput`)
- Modify: `src/phil/contracts/routing.py` (`Answer.diagnosis`)
- Modify: `src/phil/prompts/intake.md`, `architect.md`, `answer.md`
- Modify: `src/phil/chat/planning.py`
- Test: `tests/chat/test_planning.py`, `tests/chat/test_intake_spec.py`, `tests/test_contracts.py`

**Interfaces:**
- Produces:
  - `Goal.task: Task | None = None` ("Only for depth quick: the one task that does the whole change.");
  - `IntakeInput.route_depth: Literal["answer", "quick", "full"] | None = None`;
  - `IntakeInput.detected_test_cmd: str | None = None`;
  - `ArchitectInput.prior_attempt: list[AttemptWorklog] = []`;
  - `Answer.diagnosis: bool = False` ("True when the question asked why something is broken and the answer gives a cause.");
  - `intake(ctx, message, *, overview, previous=None, answers=(), call=1, route_depth=None, detected_test_cmd=None) -> Goal`;
  - `quick_plan(goal: Goal, test_cmd: str | None) -> Plan | None`;
  - `Planner.draft(goal, tree, on_step=None, *, ctx=None, prior_attempt: Sequence[AttemptWorklog] = ())`.

`quick_plan`:

```python
def quick_plan(goal: Goal, test_cmd: str | None) -> Plan | None:
    """The one-task plan for a quick goal, or None when the goal has no valid quick task (spec §4.1)."""
    task = goal.task
    if task is None:
        return None
    keyword = task.id.split("-", 1)[0]
    try:
        return Plan(keyword=keyword, description=goal.objective, tasks=[task.model_copy(update={"status": "TODO"})],
                    test_cmd=test_cmd)
    except ValidationError:
        return None
```

Prompt lines:

`intake.md`, under Goal fields:

```markdown
- `task`: only when `route_depth` is `quick`, or you choose `depth: quick` yourself, and only once `open_questions` is empty. Write the single task that does the whole change: `id` as KEYWORD-001 (3–6 uppercase letters), a one-sentence `description`, observable `acceptance_criteria`, `files_hint`, and `verify`. Use `check` with a `check_cmd` for copy, markup, config or docs. Prefer a command the repo already defines, or `detected_test_cmd`. Use `tdd` when behaviour changes and `detected_test_cmd` is set. Otherwise leave `task` null.
```

`architect.md`:

```markdown
- `prior_attempt`: when set, a quick attempt at this goal failed; its worklogs say what was tried and why it failed. Plan around those failures; don't repeat them.
```

`answer.md`:

```markdown
- `diagnosis`: true when the question asked why something is broken and your answer names a likely cause.
```

- [ ] **Step 1: Write the failing tests:**
  - `quick_plan` returns a one-task plan whose keyword is the task id's prefix;
  - it returns `None` for a goal without a task;
  - it returns `None` when the id doesn't match `^[A-Z]{3,6}-\d{3}$`. Build the `Task` with `Task.model_construct` to get an invalid id past validation;
  - `intake()` passes `route_depth` and `detected_test_cmd` into the packet: capture the payload with `ScriptedAgentFactory` and assert the rendered packet contains them;
  - `Planner.draft(..., prior_attempt=[worklog])` sends it to the architect's packet;
  - the new contract fields default correctly;
  - the prompts mention `task`, `prior_attempt` and `diagnosis`. Follow `test_intake_spec.py`'s style.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** `Planner.draft` and `_cycle` thread `prior_attempt` into `_architect`'s `ArchitectInput`. Revisions pass the same `prior_attempt`.
- [ ] **Step 4: Run tests/chat and tests/test_contracts*.py, then the full suite once.**
- [ ] **Step 5: Commit.** Message: `Let intake write a quick task and the architect see a prior attempt`, plus the trailer.

---

### Task 5: The chat runs quick changes

**Files:**
- Modify: `src/phil/chat/controller.py`
- Test: `tests/chat/test_quick_flow.py` (new), plus updates to `tests/chat/test_routing_flow.py` for the new status strings

**Interfaces:**
- Consumes: `quick_plan`, `intake(..., route_depth=, detected_test_cmd=)`, `Goal.task`, `Answer.diagnosis` (Task 4); `prepare_run(..., depth=)` (Task 1).

Behaviour:
1. **The intake job** passes `route_depth=self._route.depth if self._route else None` and `detected_test_cmd=detect_test_cmd(self.info.root)`. Use the existing `phil.repo_detect.detect_test_cmd` on the live root; only the file names are read.
2. **`_on_goal_ready`**, after the open-questions handling, when the route's depth is `quick`, or the route left the depth to intake and `goal.depth == "quick"`:
   1. `test_cmd = effective_test_cmd(<a one-task plan>, self.config, self.info.root)[0]`. The existing helper decides the test command from the plan, then phil.toml, then detection.
   2. `plan = quick_plan(goal, test_cmd)`.
   3. If `plan` is None, or `launch_problems(plan, self.config, self.info.root, check_root=self.info.root)` is non-empty: print the dim fallback line `Couldn't plan this as a quick change; planning it fully.`, record a `quick_fallback` note with the reasons, and call `self._plan(goal)` as today. The route note's depth stays `quick`, and the run records `full`.
   4. Otherwise print `Quick change: {task.id} {task.description}` (clipped), record the `quick_plan` contract, and start the run straight away with no approval prompt. Factor `_start` so it takes a `Plan` plus a `depth`, and a `PlanDraft` is no longer required. Keep `_start(draft, …)` for the full path as a thin wrapper.
3. **`depth` on every run:** pass `depth="quick"` for the quick path and `depth="full"` for the planned path.
4. **Status lines** (`_status_line`), exactly:
   - quick route: `{label} · quick path  (/full to plan it properly)`;
   - forced quick: `Forced: quick path`;
   - the fix offer: `Fix · quick path`.

   Remove the M3a "planning" wording and its "M3b" comments.
5. **The fix offer:**
   - **The prompt:** `Fix it? [Enter = quick fix / full = plan it / n]`.
   - **`_confirm_fix`:**
     - `""`, `y` or `yes` calls `_begin_goal(..., forced="quick", source="fix_offer")`, as today;
     - `full` calls `_begin_goal(f"{question}\n\nDiagnosis so far:\n{diagnosis}", forced="full", source="fix_offer")`;
     - `n` or `no` returns to idle;
     - anything else starts a new goal.
   - **When to offer:** `_on_answer_ready` offers the fix when `task_class == "diagnosis"`, or when `answer.diagnosis` is true. The second case covers forced `/ask`.

- [ ] **Step 1: Write the failing tests** in `tests/chat/test_quick_flow.py`, with the scripted chat harness from `tests/chat/test_routing_flow.py`:
  - a quick route plus intake returning a `Goal` with a valid `task`: no architect or critic calls; the output has `Quick change: FIX-001 …`; a run is spawned without an approval prompt; and `prepare_run`'s record has `depth == "quick"`. Assert through the store (`get_run`) or the spawn spy the existing tests use;
  - a quick route where intake returns no task: the fallback line is printed, the architect and critic run, the approval prompt is shown, and the approved run has `depth == "full"`;
  - a quick route where the task's `check_cmd` is refused by `launch_problems` (e.g. an absolute path outside the repo): the fallback line is printed and the full path is taken;
  - intake returns `depth="quick"` with `open_questions`: the questions are asked first, and the quick plan is used only after the answers;
  - the router defers (low confidence) and intake chooses `depth="quick"` with a task: the quick path is taken;
  - forced `/quick`: `Forced: quick path` and the quick path;
  - the fix offer: `full` at the prompt starts a forced-full goal whose intake message contains the diagnosis; Enter takes the quick path;
  - a forced `/ask` whose answer has `diagnosis=True` gets the fix offer;
  - the full path's approved run records `depth == "full"`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** Update the M3a routing-flow tests' expected strings to the new status lines. Don't weaken any assertion.
- [ ] **Step 4: Run tests/chat, then the full suite once.**
- [ ] **Step 5: Commit.** Message: `Run quick changes straight from the chat`, plus the trailer.

---

### Task 6: Moving a quick run up to full

**Files:**
- Modify: `src/phil/chat/controller.py`
- Modify: `src/phil/cli/main.py` (the `resume` answer path)
- Test: `tests/chat/test_quick_flow.py` (extend), `tests/cli/test_resume*.py` (or wherever resume answers are tested; search for it)

**Interfaces:**
- Consumes: the engine's `full` action and the `handoff/prior_attempt.json` artifact (Task 3); `Planner.draft(..., prior_attempt=)` (Task 4).

Behaviour:
1. **Choosing `full` at the paused quick run's question.** `_pause_answer` maps `full` like any option. The controller:
   1. remembers the goal: `self._full_handoff = (self._goal_text, self._goal)`;
   2. prints `Moving this to a full plan, with what the quick attempt learned.`;
   3. resumes the run with `{"action": "full"}` through the normal `_resume_run` path.
2. **When that run's `run_done` arrives** with state `aborted` and `_full_handoff` is set, the controller:
   1. reads `paths.run_dir(run_id) / "artifacts" / "handoff" / "prior_attempt.json"` (find the exact path `ArtifactStore.write_json("handoff", "prior_attempt", …)` uses);
   2. validates the worklogs as `AttemptWorklog`s, skipping invalid ones;
   3. forgets the run;
   4. calls the planning path with `prior_attempt=worklogs`. Extend `_plan(goal, prior_attempt=())` so its job passes the worklogs to `Planner.draft`.

   The architect, critic and approval follow as normal, and the approved run records `depth="full"` and starts from the current HEAD, as every new run does. The completion notice for the aborted quick run isn't shown, and nor is the PR offer.
3. **A missing or unreadable handoff file:** plan without `prior_attempt`, and record a note.
4. **`phil resume <run_id> full`** (or the terminal's answer path for paused runs; find it in `cli/main.py`) prints `full is only available in the chat that started this run; answer retry or abort.` and exits 1 without resuming.

- [ ] **Step 1: Write the failing tests:**
  - the chat: a quick run pauses with the `full` option; answering `full` spawns a resume with `{"action": "full"}`;
  - simulate the run-done event as aborted, with a handoff artifact containing one worklog. The architect's packet contains the worklog, the approval prompt follows, and the approved run is `full`;
  - a missing handoff still plans;
  - the CLI: `full` from the terminal is refused with the exact message, and no worker is spawned.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests/chat and tests/cli, then the full suite once.**
- [ ] **Step 5: Commit.** Message: `Move a failed quick run up to a full plan`, plus the trailer.

---

### Task 7: The end-to-end benchmark follows the router; docs

**Files:**
- Modify: `tests/live/bench/cases.py`, `tests/live/bench/harness.py`, `tests/live/bench/report.py`, `tests/live/bench/test_bench.py`, `tests/live/bench/test_harness_offline.py`
- Modify: `README.md`, `docs/superpowers/roadmap.md`
- Create: `docs/superpowers/plans/2026-10-01-phil-m3b-followups.md`

**Interfaces:**
- Consumes: `classify`, `decide` and `route_state` (phil.routing); `intake`, `quick_plan` and `Planner`; `ask_answer` and `export_worktree`; `prepare_run(..., depth=)`; telemetry's `model_calls`.
- Produces:
  - `Case.expect_depth: str`, with `expect_modes` kept;
  - `Case.expect_file: str | None` (answer cases: the file the answer must cite);
  - record fields `routed_depth`, `expect_depth`, `model_calls` and `minutes`;
  - report columns for depth and model calls, with the target `< 10 calls, < 1 min` marked per quick case.

Behaviour:
1. **`_run_case`** builds the route state with `route_state(case.goal, [], info.root)`, calls `classify(ctx, state)`, then `decide(judgement, …thresholds from config…)`. It records `routed_depth` (or `"intake"` when the result is None), then runs that path:
   - **quick:** `intake(ctx, case.goal, overview=…, route_depth="quick", detected_test_cmd=detect_test_cmd(tree))`, then `quick_plan(goal, effective_test_cmd(…))`. If that gives no plan, or `launch_problems` finds problems, fall back to the architect, as the chat does, and record `quick_fallback: true`. Then `prepare_run(..., depth="quick")` and `run_worker`.
   - **full**, or intake deciding: today's architect path, with `prepare_run(..., depth="full")`.
   - **answer:** `ask_answer(ctx, case.goal, root=export_worktree(info.root, chat_dir / "tree" / "answer"), overview=…)`. Record the answer's `files`. `passed` is computed by the case's own check, which should assert that `expect_file` is in the files.
2. **`model_calls`**, from the project database: `SELECT COALESCE(SUM(model_calls), 0) FROM telemetry WHERE chat_id = ? OR run_id = ?`. Check the telemetry columns in `phil.store.db.SCHEMA` and adapt if run telemetry lacks `chat_id`.
3. **`test_bench`** asserts `record["routed_depth"] == case.expect_depth`, in addition to `passed`.
4. **Cases:**
   - set `expect_depth="quick"` on `py-multiply`, `site-meta-tag` and `site-typo`;
   - add `explain-module`: fixture `py-calc`; goal `What does calc.divide do when the divisor is zero?`; `expect_depth="answer"`; `expect_file="calc.py"`; `passed` checks that the git log has no new commits and that `calc.py` is cited.

   Read the fixture to confirm `divide` exists and where it lives. Adjust the question if needed.
5. **The offline harness test** (`test_harness_offline.py`) covers the three paths with a scripted factory. It covers the routing call (a `route` script), a quick case, an answer case, and the `model_calls` sum.
6. **The report** shows depth (expected, then routed), calls and minutes, and flags a quick case that misses `< 10` calls or `< 1` minute.
7. **README:**
   - in the Routing section, describe the quick path: one task, the light implementer, no tester, one review with fixes in place, a 2-attempt cap, and `full` to move up;
   - update the `/quick` description;
   - note that `phil show` lists depth.
8. **Roadmap:** M3b is marked in progress, with links to the plan and the follow-ups.
9. **Follow-ups doc:**
   - the review patch can't change test files;
   - the quick task's `check_cmd` comes from intake;
   - revisit the per-attempt cap of 15 against the benchmark;
   - the Jev decision-rule revisit after M3 (from memory: per-backend thresholds; deferrals counted apart from wrong paths; a tolerance of at least one case; check f-01's label);
   - plus anything the reviews park.

- [ ] **Step 1: Write the failing offline harness tests.**
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the harness, cases and report, then the docs.**
- [ ] **Step 4: Run tests/live/bench offline (`uv run pytest tests/live/bench -q -n 0`; bench and live tests are deselected by default), then the full suite once.**
- [ ] **Step 5: Commit.** Message: `Route the end-to-end benchmark and measure quick runs`, plus the trailer.
