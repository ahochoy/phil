# Phil v1 — Run Engine Design

**Date:** 2026-09-23
**Status:** Approved design, pending implementation plan
**Scope:** Sub-project 1 of Phil — the run engine (story → tasks → TDD → reviewed branch). Project-level planning (PRD → roadmap → epics → stories) is sub-project 2 and gets its own spec.

## 1. Goal

Phil is a CLI coding agent. v1 takes a goal (a "story") in a local git repo, plans it with the user, then autonomously implements it in an isolated git worktree and leaves a reviewed, tested branch for the user to merge.

What sets Phil apart is **explicit contracts and managed context between agents**: every agent consumes and produces a typed contract, sees only a deliberately built context packet, and is held to code-enforced gates rather than prompt suggestions.

### Success criteria for v1

- `phil` run inside any local git repo opens a chat; a goal becomes an approved plan, and the plan becomes a background run.
- The run produces a `phil/<run-id>` branch with one commit per task, each task developed red→green and verified by code gates.
- The user's checkout is never modified.
- Runs survive terminal exit and worker crashes (resume from checkpoint).
- Token usage is recorded per agent call and viewable per run.

### Teammate principles

Phil should behave like a conscientious teammate. These principles apply to every plan, not just one feature:

- **Cleans up after itself.** Anything Phil creates (worktrees, branches, temp files, run scratch) has a defined end of life, and Phil removes it without the user running manual commands. What is worth keeping moves to project memory first. Phil owns its own state under `~/.phil` and its `phil/<run-id>` branches, so cleanup never depends on permissions a worktree-bound agent lacks.
- **Respects the codebase.** Follows the target repo's code standards, conventions, and commit style.
- **Communicates clearly.** Output to humans (chat, summaries, PR descriptions) is concise, well organized, and puts any action the reader must take up front and unmistakable (see §9a).

### MVP beyond v1

v1 ends at a reviewed branch the user merges. The MVP is the full lifecycle: goal → plan → tasks → implementation → **Phil raises the PR** → after merge, **Phil cleans up**: records learnings to project memory, removes the worktree, run branch, and temp files, and keeps only what is worth keeping. This is scheduled after plan 4 (see the follow-ups file) and builds on `WorktreeManager.remove`, the run artifacts, and the parking lot.

## 2. Decisions

| Topic | Decision |
|---|---|
| v1 slice | Goal → reviewed diff on a branch in a local repo. No GitHub, no cross-run memory. |
| Isolation | Git worktree per run; commands run on the host shell with an allowlist. Sandbox backends are a later swap. |
| Interaction | Chat REPL plus detachable background runs (`runs`, `attach`, `resume`, `stop`, `clean`). |
| Models | Tiered per role via OpenRouter, configured in `phil.toml`. Challenger roles may use a different model family. |
| Architecture | Hybrid: conversational orchestrator (chat layer) over a deterministic LangGraph run graph (run layer). |
| Tester timing | Configurable `tester_mode`: `"run"` (default, once after all tasks) or `"task+run"`. |
| Sequencing | Engine first. Project planning mode is spec #2; v1 carries a `story_ref` hook. |
| Language | Python engine. Heavy imports are lazy so lightweight commands start fast. Contracts export to JSON Schema so a native (Go/Rust) frontend can be added later without touching the engine. |
| Human interface | One choke point per direction: `ingest()` (human → `Goal`/`Feedback` contracts) and `present()` (contracts → `Brief` → `ui/`). Concise display, lossless storage. |

## 3. User experience

Phil's source lives in its own repo and is installed once as a global command (`uv tool install -e ~/Code/phil`, via `[project.scripts] phil = ...`). The user `cd`s into a target repo and runs `phil`, like other coding agents.

- **Repo resolution:** `git rev-parse --show-toplevel` from the current directory; `--repo <path>` overrides.
- **Base:** current `HEAD` commit, or `--base <branch>`. The base commit is recorded on the run. Uncommitted changes are not included; Phil warns about them at startup.
- **User checkout untouched:** runs work in `~/.phil/projects/<repo-slug>/worktrees/<run-id>` on branch `phil/<run-id>`. The branch is a normal ref in the user's repo; the user merges, cherry-picks, or deletes it.
- **Commands are scoped to the current repo.**

Example session:

```
~/code/local-vibe-exchange (main) $ phil
Phil · local-vibe-exchange · base: main @ a1b2c3d
⚠ 2 uncommitted files — not included in runs (worktree branches from HEAD)

you › add a map section that shows listings on a map
phil › Plan MAPS (5 tasks) — critic flagged 1 gap → revised: …
       Approve? [y / edit / n]
you › y
phil › Run r-7f3a started in background. Ctrl-D to detach; `phil attach r-7f3a` to watch.

$ phil runs
r-7f3a  MAPS  4/5 done  tester running   12.4k tok  $0.08
$ phil attach r-7f3a      # stream progress, answer escalations and shell approvals
$ phil diff r-7f3a        # git diff <base>...phil/r-7f3a
$ phil resume r-7f3a      # continue a crashed worker from its last checkpoint
$ phil stop r-7f3a
$ phil clean r-7f3a       # remove worktree and branch
```

**Terminal presentation:** all output goes through a single `ui/` module using a Rich `Theme` with semantic style names (`phil.agent`, `phil.gate.pass`, `phil.cost`, …). No hard-coded colors elsewhere. A branded look and feel is future work and should only require changing this module.

## 4. Architecture

Two layers:

- **Chat layer:** a conversational orchestrator the user talks to. It clarifies goals, produces an approved plan, and starts runs. It never sees run internals.
- **Run layer:** a LangGraph graph executed by a detached worker process with a SQLite checkpointer. Nodes are either plain Python (gates, git, tests) or narrowly scoped agents. Gates are code.

### 4.1 Components

```
phil/
  cli/          typer app: phil (chat), runs, attach, resume, stop, diff, clean, show, parked, _worker
  chat/         orchestrator agent and its tools
  interface/    ingest() and present(): the only paths between the human and the agents
  contracts/    Pydantic models — single source of truth for agent I/O
  agents/       AgentSpec + registry; invoke_agent()
  packets/      per-role context packet builders with token budgets
  run/          LangGraph run graph, nodes, gates, routing
  workspace/    WorktreeManager, Shell tool (allowlist, timeouts, process-group kill)
  store/        SQLite (checkpoints, runs, telemetry, parked) + on-disk artifacts
  ui/           Rich theme and all rendering
  config.py     phil.toml loading
prompts/        one markdown file per role
```

### 4.2 Boundary rules

1. Agents never call each other. They take a contract in and return a contract out. Only `chat/` and `run/` compose agents.
2. `invoke_agent(spec, packet)` is the **only** place a model is called. It builds the deep agent from the spec, validates output against the spec's `out_contract`, spot-checks evidence, records telemetry, and handles retries.
3. Run-layer agents operate only inside their run's worktree. The chat layer has read-only tools on the original repo.
4. Every agent input is a context packet built by a role-specific builder. Beyond that, agents only get what their own tool calls fetch.

### 4.3 AgentSpec

```python
class AgentSpec(BaseModel):
    name: str                      # "implementer", "tester", ...
    role: str                      # key into phil.toml models/budgets
    prompt: str                    # loaded from prompts/<name>.md
    tools: list[str]               # tool names resolved from a registry
    in_contract: type[BaseModel]
    out_contract: type[BaseModel]
```

Agents are built with `deepagents.create_deep_agent(model=..., system_prompt=..., tools=..., backend=FilesystemBackend(root_dir=worktree), response_format=out_contract)`.

### 4.4 State location

```
~/.phil/projects/<repo-slug>/
  phil.db                 SQLite: LangGraph checkpoints, runs, telemetry, parked (project-wide)
  runs/<run-id>/
    plan.json
    packets/<node>-<task>-<attempt>.json
    outputs/<node>-<task>-<attempt>.json
    assumptions.jsonl
    logs/                 full shell/test output
    summary.md
  worktrees/<run-id>/
```

## 5. Contracts

Pydantic models, each with `schema_version`. Every contract instance passed between agents is saved as JSON under the run directory, which makes any step replayable and doubles as an eval fixture.

```python
class Task(BaseModel):
    id: str                        # KEYWORD-### e.g. MAPS-001
    description: str
    acceptance_criteria: list[str]
    files_hint: list[str]          # architect's best guess at files touched
    status: Literal["TODO", "DONE", "SKIPPED", "FAILED"]

class Plan(BaseModel):
    keyword: str
    description: str
    tasks: list[Task]
    story_ref: str | None = None   # hook for project mode (spec #2), e.g. "E4/S2"
    critic_notes: list[str] = []

class Claim(BaseModel):
    statement: str
    command: str | None
    observed_output: str | None

class SelfCheck(BaseModel):
    assumptions: list[str]
    evidence: list[Claim]
    risks: list[str]
    unverified: list[str]
    out_of_scope: list[str]        # noticed but not this agent's job → parking lot

class TaskResult(BaseModel):       # implementer output
    phase: Literal["red", "green"]
    summary: str
    files_changed: list[str]
    tests_added: list[str]
    self_check: SelfCheck

class TestReport(BaseModel):       # produced by code, not an agent
    command: str
    passed: bool
    failures: list[str]            # trimmed; full log path below
    log_path: str
    new_failures_vs_baseline: list[str]

class Issue(BaseModel):
    task_id: str | None
    file: str | None
    line: int | None
    severity: Literal["blocker", "major", "minor"]
    note: str

class TesterReport(BaseModel):
    tests_added: list[str]
    issues: list[Issue]            # includes weak-test findings
    self_check: SelfCheck

class Review(BaseModel):
    verdict: Literal["approve", "changes"]
    issues: list[Issue]
    assumption_resolutions: list[str]
    self_check: SelfCheck

class RunStatus(BaseModel):        # what the chat layer sees
    run_id: str
    state: str
    tasks_done: int
    tasks_total: int
    current_node: str | None
    tokens: int
    cost_usd: float
    needs_attention: str | None


class PlanCritique(BaseModel):     # plan critic output
    verdict: Literal["ok", "revise"]
    issues: list[Issue]            # task_id set; file/line usually None
    notes: list[str]               # copied into Plan.critic_notes
    self_check: SelfCheck
```

The architect's input on a revision round is the original goal, its previous `Plan`, and the `PlanCritique`.

Human-boundary contracts (see section 9a):

```python
class Goal(BaseModel):             # ingest() output; raw user text stored alongside
    objective: str
    constraints: list[str]
    non_goals: list[str]
    open_questions: list[str]
    story_ref: str | None = None

class Ref(BaseModel):              # pointer to full detail
    label: str
    path: str                      # artifact path or run node reference

class Decision(BaseModel):
    question: str
    options: list[str]

class Brief(BaseModel):            # present() input; the only shape the user sees from agents
    headline: str = Field(max_length=120)
    status: str | None = None
    needs_you: list[Decision] = []
    points: list[Annotated[str, Field(max_length=200)]] = Field(default=[], max_length=5)
    details: list[Ref] = []
    parked: int = 0

class ParkedItem(BaseModel):
    id: str
    raised_by: str                 # "user" | "architect" | "tester" | ...
    note: str
    why_not_now: str
    source: Ref
    status: Literal["open", "promoted", "dropped"]
```

JSON Schema for every contract is exported by `phil schema` so non-Python clients can consume them.

## 6. Chat layer

The orchestrator is a deep agent on a strong model with these tools:

- `read_repo`, `grep_repo`: read-only access to the original repo.
- `plan(goal, notes) -> Plan`: runs architect → plan critic → architect revision (at most 1 revision round). Returns the plan with critic notes.
- `start_run(plan)`: only allowed once the user has approved the plan in chat. Approval is recorded by code (the REPL's `y` handler), not inferred by the model.
- `run_status(id)`, `list_runs()`: return `RunStatus` only.

The orchestrator never receives diffs, test output, or implementer reasoning unless it explicitly reads an artifact on the user's request.

User messages reach the orchestrator through `ingest()`, and the orchestrator's replies are `Brief` contracts rendered by `present()` (section 9a).

`start_run` spawns a detached worker process `phil _worker <run-id>` and returns immediately.

## 7. Run graph

The plan is already approved when the run starts.

```
setup ─► pick_task ─► implement:red ─► verify_red ─► implement:green ─► verify_green ─► commit ─► pick_task
             │             ▲               │ fail          ▲                  │ fail
             │             └───────────────┘               └──────────────────┘
             │                   (≤3 attempts per phase, then escalate)
             │
             └─ no TODO tasks ─► [tester] ─► review ─► finish
                                    │           │
                                    │           └─ changes (≤2 review rounds) ─► append fix tasks ─► pick_task
                                    └─ issues ─► append fix tasks ─► pick_task
```

| Node | Kind | Receives | Produces |
|---|---|---|---|
| `setup` | code | plan, repo, base | worktree, branch, baseline `TestReport` |
| `pick_task` | code | plan | next `TODO` task (sequential in v1) |
| `implement` | agent, cheap model | `Task`, phase, last `TestReport` on retry, relevant ledger entries | `TaskResult` |
| `verify_red` | code | worktree | pass if only test files changed and the new tests fail |
| `verify_green` | code | worktree | pass if all tests pass, no new failures vs baseline, and test files unchanged since red |
| `commit` | code | `TaskResult` | one commit per task `<ID>: <description>`; task → `DONE` |
| `escalate` | interrupt | task, last 3 reports | user choice: retry with hint / skip / abort |
| `tester` | agent, strong model, different family from implementer | plan, full diff, final `TestReport` | `TesterReport`; issues become fix tasks |
| `review` | agent, strong model | plan, full diff, final `TestReport`, assumption ledger | `Review` |
| `finish` | code | all | `summary.md`, run state complete |

Rules:

- **TDD is enforced by gates.** The implementer always works red then green; `verify_red` and `verify_green` check this in code. In the red phase, a new test failing because the symbol under test does not exist yet (import or attribute error) counts as a valid failure. Which paths count as test files is configured by `[project] test_globs` in `phil.toml`, with language defaults.
- **Retries get the failure report, not the prior transcript.** Every `implement` call starts from a fresh context.
- **All loops are capped:** 3 attempts per phase, 2 review rounds, 1 tester fix round in `"run"` mode. When a cap is hit the run escalates or finishes with open issues flagged. It never loops silently.
- **The user is interrupted only on escalation or shell approval.**
- **Tester mode `"task+run"`** adds a lightweight tester audit after each `commit` in addition to the run-level pass.
- **Future gates** (security guard, pre-deployer) slot between `review` and `finish` with the same approve/changes shape.
- The test command comes from `phil.toml` (`[project] test_cmd`) or is proposed by the architect in the plan and confirmed at approval.

## 8. Self-critique and cross-checking

- Every agent output contract includes a required `SelfCheck`. Prompts instruct a self-critique checklist within the same call; there is no extra critique call.
- **Evidence spot-checks:** `invoke_agent` verifies checkable claims (files exist, claimed commands appear in the shell log for this call). Failed checks are treated as invalid output.
- **Assumption ledger:** every `SelfCheck.assumptions` entry is appended to `runs/<id>/assumptions.jsonl` with its source node and task. Packet builders pass relevant entries forward; the reviewer must resolve each open assumption (confirm or raise an issue).
- **Cross-checks at three points:** plan critic before the user sees a plan; tester after all tasks; reviewer before finish.

## 9. Context management

- **Packet builders** (`packets/`) assemble each agent's input: contract first, then acceptance criteria, then `files_hint` file contents, then ledger entries, trimmed deterministically in that priority order to the role's budget.
- **Per-role budgets** in `phil.toml`, for example:

  ```toml
  [models]
  orchestrator = "openrouter:<strong>"
  architect    = "openrouter:<strong>"
  critic       = "openrouter:<strong, other family>"
  implementer  = "openrouter:<cheap>"
  tester       = "openrouter:<strong, other family>"
  reviewer     = "openrouter:<strong>"

  [budget.implementer]
  max_input_tokens = 12000

  [run]
  tester_mode = "run"
  max_attempts_per_phase = 3
  max_review_rounds = 2
  max_tokens = 400000
  max_cost_usd = 2.00

  [shell]
  allow = ["pytest", "pytest *", "uv run pytest", "uv run pytest *", "npm test", "git status", "git diff", "git diff *"]
  timeout_s = 300
  pass_env = []                  # secret-looking variables to pass through anyway
  ```

  Allow patterns match on argv: the program name must match exactly, remaining arguments are glob-matched per token, and a final `*` matches any remaining arguments; risky flags (`--output`, `--no-index`, `--ext-diff`, `--prefix`, `-c`, `-e`) are always denied.

  **Child-process environment:** commands inherit Phil's environment minus secret-looking variables: any name containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, or `AUTH` (case-insensitive) is removed. Names listed in `[shell] pass_env` are passed through even if they match. This keeps provider API keys out of reach of agent-written test code.

- **Large tool output is offloaded:** shell and test output over a threshold is trimmed to its beginning and end plus failure lines; the full log goes to `runs/<id>/logs/` and the agent receives the path.
- Built-in deepagents summarization and large-result offloading are used within a single agent call.
- Every packet is saved, so "what did this agent see?" always has an exact answer.
- **Terse register for agent free text:** every role prompt includes a short style block (fragments allowed, no filler, no restating inputs) for free-text contract fields such as summaries and notes. Output tokens cost several times more than input tokens, so this is where terseness pays most. Field `max_length` limits make it enforceable.

## 9a. Human interface: concise display, lossless storage

Verbose agents bury the essential point. Phil separates what is **stored** (everything, in full) from what is **shown** (layered, bounded).

- **`ingest(raw) -> Goal | Feedback`:** user messages are normalized into contracts by the orchestrator. The raw text is stored unchanged next to the contract. The user sees the normalized `Goal` at plan approval, so nuance lost in normalization is caught before any work starts. No token-dropping compression (such as LLMLingua) is applied to user intent.
- **`present(contract | RunStatus) -> Brief`:** everything shown to the user from agents is a `Brief`. Its field limits are validated like any contract; an over-long reply is rejected and retried, not displayed.
- **Progressive disclosure:** a `Brief` carries `Ref`s instead of inlined detail. `phil show <ref>` (or `/more <n>` in chat) expands one: full test log, reviewer reasoning, an agent's packet, a diff.
- **Parking lot:** anything raised that is not the current work becomes a `ParkedItem` instead of being acted on or lost. Sources: `SelfCheck.out_of_scope` from any agent, and the user via `/park <note>`. Items persist per project across runs (`parked` table in `phil.db`), are listed by `phil parked`, and their open count shows in the status line. In spec #2 they can be promoted to roadmap stories.

## 10. Failure handling

| Failure | Response |
|---|---|
| Provider error (429/5xx) | Retry with backoff in `invoke_agent`; optional `fallback_model` per role. |
| Contract validation failure | One retry with the validation error included; then the node fails and the run escalates. |
| Evidence spot-check failure | Same as validation failure; recorded as `evidence_fail`. |
| Non-allowlisted shell command | Worker pauses with an interrupt; user approves or denies via `attach`. |
| Command timeout | Kill the process group; returned to the agent as a failed command. |
| Worker crash | Heartbeat in the `runs` table; `attach` detects a dead worker and offers `phil resume`, which continues from the last checkpoint. |
| Budget exceeded | Escalate to the user. |
| Abort | Keep worktree and branch for inspection; `phil clean` removes them. |

## 11. Observability and eval hooks (hooks only in v1)

- **Telemetry:** `invoke_agent` writes one row per call to the `telemetry` table: `run_id, layer, node, role, model, attempt, packet_tokens, input_tokens, output_tokens, latency_ms, cost_usd, outcome` (`ok | invalid | evidence_fail | error`). `phil runs <id> --usage` renders totals by layer and role. `present()` also records the token size of the source contracts versus the rendered `Brief`, so the interface layer's compression is measurable. External exporters (OpenTelemetry, Langfuse) are future work.
- **Evals:** v1 guarantees that any `AgentSpec` can be invoked standalone with a packet loaded from disk, and that contract validation and evidence checks are standalone functions. Saved packets and outputs are fixtures. An eval runner (`phil eval <role> --model X`) is future work.

## 12. Testing Phil

Phil itself is built test-first.

- **Unit:** contracts (including `Brief` limits), packet builders (trimming order and budgets), parking-lot capture from `out_of_scope`, lazy-import check (lightweight commands do not import LangChain), gate rules (`verify_red`, `verify_green`), `WorktreeManager` against temporary git repos, shell allowlist matching, config loading.
- **Graph:** a `FakeAgent` returns scripted contract outputs to cover every routing path — retry caps, escalation, review round cap, tester fix tasks, both `tester_mode` values, resume after a simulated crash. No model calls.
- **Live smoke:** one end-to-end run on a small Python fixture repo with a cheap real model, opt-in via `pytest -m live`.

## 13. Out of scope for v1 (extension points noted)

| Feature | Extension point |
|---|---|
| Project planning mode (PRD → roadmap → epics → stories), spec #2 | `Plan.story_ref`; roadmap stored in the target repo (e.g. `docs/phil/roadmap.md` plus per-epic markdown with frontmatter) |
| Remote GitHub repos, opening PRs or issues | `--repo` accepting a URL; a publisher step after `finish` |
| Cross-run project memory | Assumption ledger and roadmap files |
| Security guard, pre-deployer | Gate nodes between `review` and `finish` |
| Sandbox execution (Docker, remote) | deepagents backend and Shell tool swap in `workspace/` |
| Eval runner | Standalone `AgentSpec` invocation and saved fixtures |
| Observability dashboards and exporters | `telemetry` table |
| Branded terminal look and feel | `ui/` theme module |
| Native (Go/Rust) TUI frontend | JSON Schema contract export; `present()` output as the wire format |
| Promoting parked items to roadmap stories | `ParkedItem.status = "promoted"`, spec #2 |
| Parallel task execution | `pick_task` currently sequential |
| Raise the PR and clean up after merge (MVP lifecycle, §1) | `finish` node → publisher step; `WorktreeManager.remove`; run artifacts → project memory; `phil clean` |

## 14. Relationship to existing code

The existing prototype (`graph.py`, `nodes/`, `prompts/`, `entrypoint.py`) informs this design but is not preserved as-is. The architect prompt carries forward into `prompts/architect.md` with the new `Task` fields. Known issues in the prototype (missing `state.py`, `entrypoint.py` referencing undefined names, demo code running on import, `.env` not gitignored) are superseded by the new package layout.
