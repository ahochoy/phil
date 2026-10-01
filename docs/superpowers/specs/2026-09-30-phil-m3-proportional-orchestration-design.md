# Phil M3: Proportional Orchestration

**Status:** approved in conversation, 2026-09-30.

**Scope:** roadmap M3. Phil classifies every chat message and gives it only as much process as it needs:
- questions are answered in chat, with no run;
- small changes get one task and a light run;
- larger work keeps today's full pipeline.

Routing is done by a classifier: TypeSafe's Jev, a fast typed-judgement model, or the `low` model as a fallback. A benchmark measures whether the dedicated classifier is worth having.

M3 ships in two plans:
- **M3a, routing:** the classify primitive, the backends, the routing policy, path overrides, the answer path, the setup step and the classifier benchmark.
- **M3b, quick path:** quick intake, the quick run depth, escalation to full, and depth checks in the end-to-end benchmark.

## 1. Problem

- **Every message pays for the full pipeline.** Today every chat input goes through intake, architect, critic, a run, the tester and the review (`ChatController._begin_goal`, `chat/controller.py:495`). There is no question-versus-change detection. Only `/btw` answers without a run.
- **Small changes are expensive.** M1's benchmark measured about 25–30 model calls, about 100k tokens and about 2 minutes for a one-line change. Most of the calls come from the deep implementer's tool loop (with its general-purpose sub-agent and summarization), the tester pass, and review findings that become new TDD tasks.
- **The `classifier` tier is unused.** M2a added it to config (it falls back to `low`), but nothing uses it.

## 2. Decisions (user, 2026-09-30)

| Topic | Decision |
|---|---|
| Depths | Three: **answer** (a reply in chat, read-only, no run), **quick** (one task, a light run), **full** (today's pipeline). The classifier names one of nine task classes, and a fixed table maps each class to a depth. |
| Misjudgement | Show the choice and proceed. A one-line status shows the class and path. `/ask`, `/quick` and `/full` force a path for one message. A failing quick run offers to move up to full. |
| Quick run | One worker, the relevant gate, and one lean review. Review findings are patched within the same task. No tester. Retries are capped at 2. |
| Classifier | A **decision primitive**, not a pipeline stage. A typed-judgement model (TypeSafe Jev) answers typed questions, with no generated text. Generative intake stays for writing goals, tasks and open questions. |
| Jev access | Plain `httpx` to the HTTP API, not `typesafe-sdk`. It is one endpoint, `httpx` is already a dependency, and Phil keeps control of retries (the lesson of M2a's OpenRouter SDK retry loop). |
| Proof | A classifier benchmark with a written decision rule (§5.1). |

## 3. Routing (M3a)

### 3.1 Task classes and depths

| Class | Depth |
|---|---|
| `question` (a question or explanation) | answer |
| `diagnosis` (find the cause of a bug, no edit) | answer |
| `small_operation` (a small deterministic operation) | quick |
| `simple_change` | quick |
| `focused_fix` | quick |
| `feature` | full |
| `refactor` | full |
| `design` (architecture or design work) | full |
| `broad_project` (broad, multi-file work) | full |
| `other` | goes to intake (§3.3) |

The table lives in code (`phil.routing.policy`). The class descriptions and examples sent to the classifier live in one module, so the benchmark and the runtime use exactly the same wording.

### 3.2 The classify primitive (`phil.routing`)

```python
@dataclass(frozen=True)
class Judgement:
    task_class: str                 # one of the class keys, or "other"
    probabilities: dict[str, float] # per class; one-hot for backends without a distribution
    confidence: float               # 0..1
    needs_detail: float             # probability the request must be clarified first
    source: Literal["jev", "llm", "fake"]
    latency_ms: int
    usage: Usage | None             # tokens, and cost where known

class Classifier(Protocol):
    def judge(self, state: RouteState) -> Judgement: ...
```

`RouteState` is a JSON-serializable dict:

```json
{
  "request": "<the user's message>",
  "chat": ["<up to the last 4 turns, each trimmed to 400 chars>"],
  "repo": {"test_cmd": "npm test", "files": ["<up to 60 tracked paths>"], "file_count": 0}
}
```

The `repo` fields come from what Phil already has: `detect_test_cmd` and `git ls-files`. M3 adds no new detection.

### 3.3 Questions asked

One request per message, with two questions answered in parallel:
- **`task_class`** — a Choice over the nine classes plus `other`. Each option has a description and two short examples.
- **`needs_detail`** — a yes/no question: "Is `request` too ambiguous to act on without first asking the user something (missing target, conflicting goals, or no way to tell what done means)?"

### 3.4 Backends

- **`jev`:**
  - `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer <key>`, `model` from config (`jev-latest` by default), the `state` above, and the two questions as Choice and Noul.
  - It parses `answers.task_class.{choice, probabilities, confidence}`, `answers.needs_detail.noul` and `usage`.
  - Timeout: 5 s for connect and read. No retries inside the adapter.
- **`llm`:** the model the `classifier` role resolves to (falling back to `low`), as a lean agent with structured output:

  ```
  {task_class: Literal[...], confidence: float, needs_detail: float}
  ```

  The same descriptions and examples go into the prompt. `probabilities` is one-hot on the chosen class. This is the default when no `typesafe` classifier is configured.
- **`fake`:** scripted judgements, for tests.

**Failure:** any `jev` error (timeout, a 401/422/429/529 response, a malformed body, or a missing key) falls back to `llm` for that message. Phil shows one dim status line (`Router unavailable (429); using your low model.`) and records the error class. Routing never blocks chat. A `llm` failure falls through to intake (§3.5); when Jev failed first (or there is no `low` model to fall back to), the line reads `Router unavailable ({reason}); intake decides.` and the route record keeps the reason.

### 3.5 Routing policy (code, not model)

Evaluated in order:

| Condition | Route | `source` recorded |
|---|---|---|
| The message starts with `/ask`, `/quick` or `/full` | that depth; the prefix is stripped | `forced` |
| `needs_detail >= 0.6` | generative intake: writes the open questions, then returns its own depth | `intake` |
| `task_class == "other"` or `confidence < threshold` | generative intake decides the depth | `intake` |
| otherwise | the class's depth from §3.1 | `jev` or `llm` |

- `routing.confidence_threshold` defaults to `0.5`, `routing.detail_threshold` to `0.6`. Both can be set in config and are tuned by the benchmark (§5.1).
- Intake's existing `Goal` contract gains `depth: Literal["answer", "quick", "full"]`, used whenever intake decides.
- A forced depth skips the classifier call entirely.

**Status line:** `Simple change · quick path  (/full to plan it properly)`. When intake or a prefix decided, it says so (`Forced: full path`, `Unclear request · asking first`).

**Record:** every message appends `{class, depth, source, confidence, needs_detail, latency_ms}` to the chat's event log, and every run records its `depth`.

### 3.6 Answer path

- A new role, **`answerer`**, on the `low` tier (`DEFAULT_TIERS`). It is a deep agent with **read-only** tools only:
  - `ls`, `read_file`, `glob`, `grep`;
  - a shell tool limited to M1's read-only commands. The project allowlist (`shell.allow`, e.g. `pytest`) does not apply, because running tests can write files.

  It has no write or edit tools, no general-purpose sub-agent and no summarization. It reads a snapshot of the working tree (tracked and untracked, non-ignored files), so uncommitted edits are visible and ignored files such as `.env` are not.
- **Call cap:** 12 model calls, enforced through the recursion limit. At the cap, it answers with what it has found so far and says so.
- **Output contract:** `Answer {text: str, files: list[str]}`. It is shown in chat and recorded in the chat log. It creates no run, worktree or commit.
- **Diagnosis:** the reply ends with `Fix it? (Enter = quick fix, /full = plan it)`. Accepting starts the quick or full path, with the answer passed to intake as context.
- `/btw` is unchanged. It answers about the *current* run and is a separate feature.

### 3.7 Config, provider and keys

- A new **role `classifier`**, mapped to the `classifier` tier, which falls back to `low` as in M2a. An empty `[models] classifier` means the `llm` backend runs on the `low` model.
- A new built-in **provider `typesafe`**, of kind `systemone`:
  - key variable `TYPESAFE_API_KEY`;
  - base URL `https://api.typesafe.ai/v1`.

  Its model IDs are passed through unchanged (`typesafe:jev-latest`). `typesafe` is valid only for the `classifier` role. Setting it on any other role is a config error, naming the role.
- Keys come from `phil.key_store`. `phil keys set|list|remove typesafe` works unchanged.
- **`phil models check`:** for `typesafe`, it sends one tiny request (a two-option Choice) and reports `✓ classifier typesafe:jev-latest 0.3s`, or the error in plain words.
- **Setup:** one optional step after the `high`/`low` models:

  ```
  Route requests with a fast classifier?
    TypeSafe Jev  (needs a key)
    your low model  (default)
  ```

  Choosing Jev runs the M2b hidden key prompt and writes only `[models] classifier`.

### 3.8 Between M3a and M3b

M3a routes and records the `quick` depth, but quick goals still go through the full pipeline until M3b lands. The status line says `Simple change · planning`, not "quick path", so it doesn't claim a shortcut that isn't there yet. Diagnosis's `Fix it?` offer starts a normal goal.

## 4. Quick path (M3b)

Revised 2026-10-01 against the code M3a shipped. The user approved these points before the M3b plan.

### 4.1 Planning

- **Intake writes the one task.** `Goal` gains an optional `task: Task`. Intake fills it when the route is `quick` (from the router, a forced `/quick`, or the "Fix it?" offer), or when intake itself chooses `depth="quick"`. `IntakeInput` gains `route_depth` and `detected_test_cmd`, so intake can follow M1's rules:
  - the `verify` mode;
  - a `check_cmd` for `check` tasks, ideally one the repo already defines.
- **Phil builds the plan in code:** `Plan(keyword=<the task id's prefix>, description=goal.objective, tasks=[task], test_cmd=<detected>)`, using the existing `Task` and `Plan` validation.
- **If the task is missing or fails validation**, Phil falls back to the full path (architect and critic) and says so in one dim line. A bad draft never reaches a run.
- The **architect and critic are skipped.** The one-task plan is shown as a single line, and the run starts straight away with no approval prompt.

### 4.2 Run (`depth="quick"`)

- **Depth on the run:**
  - a migration adds `runs.depth TEXT`;
  - `prepare_run` records it, `RunState` carries it, and `phil show` displays it.

  This closes M3a's follow-up. Full runs record `full`.
- **The same `RunEngine` graph, with these quick-run differences:**
  - **No tester:** the run starts with `tester_done` already set, so `route_after_pick` goes straight to review, and `route_after_commit` never goes to `tester_task`.
  - **Attempts:** `max_attempts_per_phase` is `2` for quick runs (`run.quick_max_attempts`, default 2).
  - **Lighter implementer:** the `quick_implementer` spec, on the light harness with writes allowed.
    - Writes go through `FilesystemMiddleware` with `filesystem_permissions(spec)`, so `.git/**` and `phil.toml` are still denied.
    - Eviction of large tool results stays off.
    - There's no sub-agent and no summarization.
    - A hard stop applies: the call budget's soft step is at `max_model_calls`, and the hard stop 2 calls later, with **`max_model_calls = 15` per attempt**.
  - **Lean review, once.**
    - Blocking or major findings aren't turned into tasks (`issues_to_tasks` isn't used). The run reopens the same task as a check task, with each finding added to its acceptance criteria as `Review: <note>`. It makes **one** fix attempt, reruns the gate, and commits the fix as `<id>: fix after review`. The fix may edit test files that the original task created or changed; every other test file stays frozen.
    - If the run is aborted or moved to full while fixing, the findings stay listed in the run's open issues as `not fixed after review`.
    - There is no second review after the fix.
    - Minor findings appear only in the summary.

### 4.3 Moving up to full

- **Trigger:** the gate still fails after 2 attempts, or the fix attempt doesn't clear the gate.
- **Escalation options:** `["full", "retry", "abort"]` for runs started from a chat; `["retry", "abort"]` for runs started with `phil run`. Choosing `full` in the chat:
  1. ends the quick run as aborted (through the normal resume path);
  2. sends the original goal and the quick run's worklogs to the architect (`ArchitectInput` gains an optional `prior_attempt: list[AttemptWorklog]`);
  3. runs the normal architect, critic and approval flow;
  4. starts a full run from the base branch, not from the quick run's commits.

### 4.4 Full path

Unchanged, apart from the classifier call at the front and the recorded depth.

### 4.5 Chat

- **Status lines:**
  - quick: `{label} · quick path  (/full to plan it properly)`;
  - forced quick: `Forced: quick path`;
  - the fix offer: `Fix · quick path`.

  This replaces the M3a "planning" wording from §3.8.
- **The "Fix it?" prompt** becomes `Fix it? [Enter = quick fix / full = plan it / n]`.
  - Enter or `y` starts a quick goal with the diagnosis as context.
  - `full` starts a full goal.
  - A forced `/ask` whose answer was a diagnosis also gets the offer. The answerer's reply decides whether it was one, because forced answers have no classifier class.

## 5. Benchmarks

### 5.1 Classifier benchmark (M3a)

- **Location:** `tests/live/bench/classify/`, run with `-m bench`.
- **Data:** `cases.jsonl`, about 40 labelled requests, each `{request, repo, expected_class, expected_depth, ambiguous}`:
  - questions, explanations and diagnoses;
  - typos, config tweaks and one-line fixes;
  - features, refactors and design work;
  - about 6 deliberately vague requests.

  Requests vary in length and style, and some mix a question with a change.
- **Runs:** every case through each configured backend (`jev`, and `llm` on the current `low`/`classifier` model).
- **Records:** raw answers (probabilities, confidence, `needs_detail`), latency and usage, appended to `~/.phil/bench/classify.jsonl`.
- **Report:**
  - depth accuracy and class accuracy;
  - a depth confusion matrix that singles out *quick mistaken for full*, *full mistaken for quick*, and *answer mistaken for change* (the worst error: an unwanted edit);
  - `needs_detail` precision and recall on the vague cases;
  - p50/p95 latency and cost per 100 requests;
  - a **threshold sweep** (0.3 to 0.9, in steps of 0.1), replayed from stored answers with no new calls. It shows accuracy, the share of requests that fall through to intake, and the cost of those fall-throughs.

**Decision rule.** Jev is worth recommending over the `llm` backend if all three hold:
- its depth accuracy is no more than 2 points lower, and it makes no more *answer mistaken for change* errors;
- its p95 latency is at least 3× lower;
- its cost per routed message is lower.

If Jev wins, setup recommends it. If it doesn't, it stays optional. Either way, the results go into journal part 3. The chosen thresholds become the config defaults.

### 5.2 End-to-end benchmark (M3b)

- **Each case goes through the real router.** The harness calls `classify` and `decide` on the goal and **asserts the expected depth**. This replaces the record-only `expect_modes`, which stays as a record. `py-multiply`, `site-meta-tag` and `site-typo` should take the quick path.
- **The harness then runs the path the router chose:**
  - quick: intake with `route_depth="quick"`, then the one-task plan and a quick run;
  - full: the architect and critic, as today;
  - answer: the answerer on the working-tree snapshot.
- **New case `explain-module`:** a question about the fixture repo. It must take the answer path, create no run and no commits, and the reply must cite the right file.
- **Targets against M1's numbers:** a quick case finishes in **under 10 model calls and under 1 minute**. These are recorded per case (calls are summed from telemetry's `model_calls` for the chat and the run) and shown in the report against the target. They aren't asserted, because live models vary.

## 6. Testing (offline, no network, no real keychain)

- **Jev adapter:**
  - request shape and response parsing against recorded fixtures;
  - 401, 422, 429, 529, timeouts and malformed bodies each fall back to `llm`;
  - the key never appears in output or logs (checked with a planted secret).
- **Routing policy:**
  - every combination of override, `needs_detail`, `confidence` and class;
  - the thresholds read from config.
- **Overrides:** `/ask`, `/quick` and `/full` strip the prefix and skip the classifier.
- **Answerer:**
  - it has no write tools;
  - a write attempt through the shell is refused by the allowlist;
  - the call cap is enforced;
  - it creates no run.
- **Quick engine:**
  - there is no tester node;
  - findings are patched in the same task, with no new tasks;
  - attempts are capped at 2;
  - escalation to `full` carries the worklog and starts from the base branch.
- **Config:**
  - `typesafe` on a non-classifier role is a config error;
  - the `classifier` role falls back to `low`.
- **Other:** `models check` for `typesafe`, and setup's classifier step (scripted input).

## 7. Global constraints

- Phil never prints or logs key values. Agent-run commands keep the null keyring backend from M2b.
- There are no hidden retries inside any provider client, including Jev.
- Import rule: `phil.routing.*`, `phil.cli.main`, `phil.chat.*` and `phil.config` must not import langchain, langgraph or deepagents at module level.
- No new runtime dependency for Jev (`httpx` only). Any dependency touched is upgraded to its current version (the keep-current rule).
- The user runs the live and bench tests.

## 8. Out of scope

- Using the classify primitive for other decisions: tdd versus check mode, whether a chat reply during a run is an answer or a new goal, or whether a review finding blocks. The primitive is built so these can be added later.
- Codebase-orientation digests (a parked spike).
- Permissions (M4) and the live TUI view of routing (M5).
