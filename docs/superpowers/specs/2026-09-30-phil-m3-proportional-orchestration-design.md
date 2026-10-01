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

**Failure:** any `jev` error (timeout, a 401/422/429/529 response, a malformed body, or a missing key) falls back to `llm` for that message. Phil shows one dim status line (`Router unavailable (429); using your low model.`) and records the error class. Routing never blocks chat. A `llm` failure falls through to intake (§3.5).

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

  It has no write or edit tools, no general-purpose sub-agent and no summarization. It reads the live repository root, so uncommitted edits are visible.
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

### 4.1 Planning

- **Intake** (generative, on the `low`/`orchestrator` model) writes the goal and **exactly one task**:
  - the description and acceptance criteria;
  - the `verify` mode and, for `check`, the `check_cmd`, chosen by M1's rules from the detected `test_cmd` and check commands.
- The **architect and critic are skipped.** The one-task plan is shown as a single line, and the run starts straight away.

### 4.2 Run (`depth="quick"`)

The same `RunEngine` graph, with these differences:
- **Lighter implementer:** the deep agent without the general-purpose sub-agent and summarization middleware. It is built by `agents/factory.py` from a `quick` flag on the spec.
- `max_attempts_per_phase = 2`.
- **No tester pass:** `route_after_commit` and `route_after_pick` skip `tester`/`tester_task`.
- **Lean review, once.** Blocking or major findings are not turned into tasks (`issues_to_tasks` is not used). The run reopens the same task with the findings as `last_problems`, makes **one** patch attempt, and reruns the gate. Minor findings appear only in the summary.

### 4.3 Moving up to full

- **Trigger:** the gate still fails after 2 attempts, or the patch attempt doesn't clear a blocking finding.
- **Escalation:** the options are `["full", "retry", "abort"]`. Choosing `full`:
  1. ends the quick run;
  2. sends the original goal and the quick run's worklog (M1) to the architect;
  3. starts a full run from the base branch, not from the quick run's commits.

### 4.4 Full path

Unchanged, apart from the classifier call at the front and the recorded depth.

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

- Each existing case **asserts** its expected depth, replacing the record-only `expect_modes`: `py-multiply`, `site-meta-tag` and `site-typo` should take the quick path.
- **New case `explain-module`:** a question about the fixture repo. It must take the answer path, create no run and no commits, and the reply must cite the right file.
- **Targets against M1's numbers:** a quick case finishes in **under 10 model calls and under 1 minute**.

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
