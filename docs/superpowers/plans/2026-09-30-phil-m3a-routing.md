# Phil M3a: Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every chat message is classified and routed to an answer (a read-only reply in chat, with no run), a quick change or a full plan. Routing uses TypeSafe Jev when configured, otherwise the `low` model, and a benchmark measures both.

**Architecture:**
- A new `phil.routing` package holds:
  - the task classes and the class-to-depth table;
  - the `Judgement` type;
  - the routing policy (pure code);
  - two backends: Jev over plain `httpx`, and an LLM fallback as a lean agent.
- The chat controller runs one routing job before intake. Answers go to a new read-only `answerer` agent built on a new "light" harness: LangChain's `create_agent` plus deepagents' `FilesystemMiddleware` restricted to read tools, with no sub-agent and no summarization.
- The quick depth is routed and recorded, but still planned with the full pipeline until M3b (spec §3.8).

**Tech stack:** Python 3.14, uv, pydantic, httpx, LangChain 1.4 (`create_agent`, `AgentMiddleware`), deepagents 0.7.19 (`FilesystemMiddleware`, `FilesystemBackend`), tomlkit, pytest (`-n auto`).

**Spec:** `docs/superpowers/specs/2026-09-30-phil-m3-proportional-orchestration-design.md`. This plan implements §3, §5.1 and the M3a rows of §6.

## Global Constraints

- **Keys:** never print or log key values. Keys are read through `phil.key_store.key_lookup()`. Errors name the variable, never the value. Agent-run commands keep the null keyring backend from M2b.
- **No hidden retries.** The Jev adapter makes exactly one HTTP attempt per message: timeout 5 s, no retry loop, no SDK.
- **Import rule:** `phil.routing.*`, `phil.cli.main`, `phil.chat.*` and `phil.config` must not import langchain, langgraph or deepagents at module level. Import `httpx` lazily inside functions in `phil.routing.jev`.
- **No new runtime dependency.** `httpx` is already one (M2b). Do not add `typesafe-sdk`.
- **Jev API:**
  - `POST {base_url}/systemone`, base URL `https://api.typesafe.ai/v1`;
  - headers `Authorization: Bearer <key>` and `Content-Type: application/json`;
  - body `{"model", "state", "questions"}`;
  - Choice answers carry `choice`, `probabilities` and `confidence`; Noul answers carry `noul`; the response has a top-level `usage` with `input_tokens` and `output_tokens`;
  - errors are 401, 422, 429 and 529.
- **Provider:** the built-in provider `typesafe` has kind `systemone`, key variable `TYPESAFE_API_KEY`, and default model `typesafe:jev-latest`. It is valid only for the `classifier` role.
- **Defaults:** `routing.confidence_threshold = 0.5`, `routing.detail_threshold = 0.6`, `routing.jev_timeout_s = 5`.
- **Answerer call cap:** `ANSWER_MAX_MODEL_CALLS = 12`.
- **Exact user-facing strings:**
  - `Router unavailable ({reason}); using your low model.`
  - `Fix it? [Enter = plan the fix / n] › `
- **Tests** never touch the network or the real keychain. Live and bench tests are opt-in (`-m live`, `-m bench`), and the user runs them.
- **Commits:** every message ends with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The user's shell guard blocks Bash command lines containing the words "keychain" or "credentials", so write commit messages to a file and run `git commit -F <file>`.
- **Never work around a hook, guard or permission denial.** Stop and report BLOCKED.

## Review Focus

1. **A message that is only an override prefix** (`/ask` with nothing after it). Expected: a usage hint, no routing job and no empty goal. Tested in Task 2 (`parse_override`) and Task 7.
2. **A Jev response missing `answers.task_class`, or with a `choice` that isn't one of the classes.** Expected: a `JevError("malformed")`, then the `llm` fallback. It must not crash or route to a made-up depth. Tested in Task 3.
3. **Jev configured but `TYPESAFE_API_KEY` unset.** Expected: chat still starts (`classifier` isn't a required chat role). The status line says the router is unavailable (`missing key`) and the `low` model routes. Tested in Tasks 4 and 7.
4. **The answerer tries to run `pytest`, or a write command, through the shell.** Expected: denied. Only M1's read-only commands run. Tested in Task 5.
5. **A new message typed while routing or answering is in flight.** Expected: the existing confirm-replace prompt; a stale result is dropped by its generation. Tested in Task 7.

---

## File structure

| File | Responsibility |
|---|---|
| `src/phil/config.py` (modify) | `classifier` and `answerer` roles; `RoutingConfig`; refuse a `systemone` model on a non-classifier role |
| `src/phil/agents/providers.py` (modify) | the `typesafe` built-in provider; `build_chat_model` refuses the `systemone` kind |
| `src/phil/routing/__init__.py` | public re-exports |
| `src/phil/routing/classes.py` | the class keys, descriptions, examples, the depth table, and the yes/no question text |
| `src/phil/routing/types.py` | `Judgement`, `Usage`, `RouteState`, `route_state()` |
| `src/phil/routing/policy.py` | `parse_override`, `decide`, `Route` |
| `src/phil/routing/jev.py` | the Jev HTTP client: `JevError`, `build_request`, `parse_response`, `judge_jev` |
| `src/phil/routing/llm.py` | the LLM backend: `judge_llm` |
| `src/phil/routing/classify.py` | `classify()`: backend choice and fallback |
| `src/phil/contracts/routing.py` | `RouteInput`, `RouteJudgement`, `AnswerInput`, `Answer` |
| `src/phil/agents/spec.py` (modify) | the `light` harness; `read_only_shell`; `max_model_calls` |
| `src/phil/agents/factory.py` (modify) | `_build_light_agent`; the model-call budget middleware |
| `src/phil/agents/invoke.py` (modify) | a read-only shell policy for `read_only_shell` specs |
| `src/phil/agents/registry.py` (modify) | the `route` and `answer` specs |
| `src/phil/prompts/route.md`, `src/phil/prompts/answer.md` | prompts |
| `src/phil/prompts/intake.md` (modify) | the `depth` field |
| `src/phil/contracts/interface.py` (modify) | `Goal.depth` |
| `src/phil/chat/answer.py` | `ask_answer()` |
| `src/phil/chat/controller.py` (modify) | the routing job, overrides, the answer stage, the diagnosis follow-up, status lines, records |
| `src/phil/ui/answer_view.py` | `render_answer()` |
| `src/phil/agents/check.py` (modify) | `models check` for `systemone` |
| `src/phil/setup/flow.py` (modify) | the classifier step |
| `tests/live/bench/classify/*` | the classifier benchmark: cases, runner, metrics, report |
| `README.md`, `docs/superpowers/roadmap.md`, `docs/superpowers/plans/2026-09-30-phil-m3a-followups.md` | docs |

---

### Task 1: Config roles, routing settings and the `typesafe` provider

**Files:**
- Modify: `src/phil/config.py`
- Modify: `src/phil/agents/providers.py`
- Test: `tests/test_config.py`, `tests/agents/test_providers.py`

**Interfaces:**
- Produces:
  - `ROLES` gains `"classifier"` and `"answerer"`;
  - `DEFAULT_TIERS["classifier"] = "classifier"` and `DEFAULT_TIERS["answerer"] = "low"`;
  - `CHAT_ROLES` gains `"answerer"` (not `classifier`, since routing falls back);
  - `PhilConfig.routing: RoutingConfig` with `confidence_threshold: float = 0.5`, `detail_threshold: float = 0.6` and `jev_timeout_s: float = 5.0`;
  - `BUILTIN_PROVIDERS["typesafe"] = ProviderSpec("typesafe", "systemone", "https://api.typesafe.ai/v1", "TYPESAFE_API_KEY", None, None)`;
  - `SYSTEMONE = "systemone"` in `providers.py`;
  - `PhilConfig.is_systemone(role) -> bool`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`:

```python
import pytest

from phil.config import CHAT_ROLES, DEFAULT_TIERS, ROLES, ConfigError, PhilConfig


def test_classifier_and_answerer_roles_resolve_through_their_tiers():
    config = PhilConfig(models={"high": "openrouter:h", "low": "openrouter:l"})
    assert "classifier" in ROLES and "answerer" in ROLES
    assert DEFAULT_TIERS["classifier"] == "classifier"
    assert config.model_for("classifier") == "openrouter:l"  # classifier tier falls back to low
    assert config.model_owner("classifier") == "low"
    assert config.model_for("answerer") == "openrouter:l"
    assert "answerer" in CHAT_ROLES and "classifier" not in CHAT_ROLES


def test_routing_defaults_and_bounds():
    config = PhilConfig()
    assert config.routing.confidence_threshold == 0.5
    assert config.routing.detail_threshold == 0.6
    assert config.routing.jev_timeout_s == 5.0
    with pytest.raises(ValueError):
        PhilConfig.model_validate({"routing": {"confidence_threshold": 1.5}})
    with pytest.raises(ValueError):
        PhilConfig.model_validate({"routing": {"jev_timeout_s": 0}})


def test_typesafe_classifier_is_allowed():
    config = PhilConfig(models={"low": "openrouter:l", "classifier": "typesafe:jev-latest"})
    assert config.model_for("classifier") == "typesafe:jev-latest"
    assert config.is_systemone("classifier") is True
    assert config.is_systemone("orchestrator") is False


@pytest.mark.parametrize(
    "models",
    [
        {"low": "typesafe:jev-latest"},  # every low role, not just the classifier
        {"low": "openrouter:l", "orchestrator": "typesafe:jev-latest"},
    ],
)
def test_typesafe_on_any_other_role_is_a_config_error(models):
    with pytest.raises(ValueError, match="typesafe.*only.*classifier"):
        PhilConfig(models=models)
```

In `tests/agents/test_providers.py`:

```python
import pytest

from phil.agents.providers import BUILTIN_PROVIDERS, SYSTEMONE, build_chat_model
from phil.config import ConfigError


def test_typesafe_is_a_builtin_systemone_provider():
    spec = BUILTIN_PROVIDERS["typesafe"]
    assert spec.kind == SYSTEMONE == "systemone"
    assert spec.base_url == "https://api.typesafe.ai/v1"
    assert spec.api_key_env == "TYPESAFE_API_KEY"


def test_systemone_never_builds_a_chat_model():
    with pytest.raises(ConfigError, match="classifier"):
        build_chat_model(BUILTIN_PROVIDERS["typesafe"], "jev-latest", 5, {"TYPESAFE_API_KEY": "x"})
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_config.py tests/agents/test_providers.py -q -n 0`
Expected: FAIL (ImportError for `SYSTEMONE`, KeyError `typesafe`, AttributeError `routing`).

- [ ] **Step 3: Implement**

In `src/phil/config.py`:

```python
ROLES = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer", "classifier", "answerer")
TIERS = ("high", "low", "classifier")
DEFAULT_TIERS: dict[str, str] = {
    "architect": "high",
    "critic": "high",
    "reviewer": "high",
    "orchestrator": "low",
    "implementer": "low",
    "tester": "low",
    "classifier": "classifier",
    "answerer": "low",
}
# Roles the chat calls; `phil` checks these have models before the conversation starts. The
# classifier isn't one: routing falls back to the low model, then to intake.
CHAT_ROLES = ("orchestrator", "architect", "critic", "answerer")


class RoutingConfig(_Section):
    confidence_threshold: float = 0.5  # below it, intake decides the depth (spec §3.5)
    detail_threshold: float = 0.6  # at or above it, intake asks the user first
    jev_timeout_s: float = 5.0

    @field_validator("confidence_threshold", "detail_threshold")
    @classmethod
    def _validate_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("routing thresholds must be between 0 and 1")
        return value

    @field_validator("jev_timeout_s")
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("routing.jev_timeout_s must be > 0")
        return value
```

- Add `routing: RoutingConfig = RoutingConfig()` to `PhilConfig`, after `providers`.
- Add an `@model_validator(mode="after")` named `_check_systemone_roles` that, for each role in `ROLES` other than `"classifier"`, calls `self.is_systemone(role)`. On the first `True`, raise:

  `ValueError(f"models for {role} resolve to {model}: the typesafe provider (kind systemone) only answers routing questions, so it can be set only for the classifier ([models] classifier).")`

  Import `model_validator` from pydantic.
- Add the method:

```python
    def is_systemone(self, role: str) -> bool:
        """True when `role`'s model is on a typed-judgement (systemone) provider such as typesafe."""
        from phil.agents.providers import SYSTEMONE, UnknownProvider, resolve_provider, split_model

        try:
            model = self.model_for(role)
        except ConfigError:
            return False
        try:
            return resolve_provider(self, split_model(model)[0]).kind == SYSTEMONE
        except UnknownProvider:
            return False
```

`PhilConfig` model validators run after field validation, and `resolve_provider` only reads `self.providers`, so calling it from the validator is safe.

In `src/phil/agents/providers.py`:
- Add `SYSTEMONE = "systemone"`.
- Add `"typesafe": ProviderSpec("typesafe", SYSTEMONE, "https://api.typesafe.ai/v1", "TYPESAFE_API_KEY", None, None)` to `BUILTIN_PROVIDERS`.
- Update the `kind` comment on `ProviderSpec` to list `"systemone"` (typed judgements; never a chat model).
- At the top of `build_chat_model`, before the key lookup:

```python
    if spec.kind == SYSTEMONE:
        raise ConfigError(
            f"{spec.name} answers typed routing questions, not chat; use it only for the classifier."
        )
```

`ProviderConfig.kind`'s `Literal` stays as it is: custom providers can't be `systemone`. Only the built-in `typesafe` is.

- [ ] **Step 4: Run them and confirm they pass**

Run: `uv run pytest tests/test_config.py tests/agents/test_providers.py -q -n 0`
Expected: PASS. Then run `uv run pytest -q`; the full suite should pass. Several existing tests iterate over `ROLES` or `CHAT_ROLES`. Update any that hard-code the old six-role tuple so they expect the new roles. Don't weaken their assertions.

- [ ] **Step 5: Commit**

Write the message to `/tmp/phil-msg` (or your scratch directory) and run `git add -A && git commit -F <file>`:

```
Add classifier and answerer roles, routing settings and the typesafe provider

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

---

### Task 2: Routing core: classes, judgements and the policy

**Files:**
- Create: `src/phil/routing/__init__.py`, `src/phil/routing/classes.py`, `src/phil/routing/types.py`, `src/phil/routing/policy.py`
- Test: `tests/routing/__init__.py`, `tests/routing/test_policy.py`, `tests/routing/test_types.py`

**Interfaces:**
- Consumes: `PhilConfig.routing` (Task 1).
- Produces:
  - `CLASSES: dict[str, ClassInfo]` (nine keys plus `"other"`), `DEPTH: dict[str, str]`, `DEPTHS = ("answer", "quick", "full")`;
  - `NEEDS_DETAIL_QUESTION: str`, `NEEDS_DETAIL_CRITERIA: dict[str, str]`, `TASK_CLASS_QUESTION: str`;
  - `Usage(input_tokens: int, output_tokens: int, cost_usd: float | None)`;
  - `Judgement` (fields in Step 3);
  - `RouteState = dict[str, object]`, built by `route_state(request, chat, root) -> RouteState`;
  - `parse_override(text) -> tuple[str | None, str]`;
  - `decide(judgement: Judgement | None, *, confidence_threshold: float, detail_threshold: float) -> tuple[str | None, str]`;
  - `Route(depth: str | None, source: str, reason: str, text: str, judgement: Judgement | None)`.

- [ ] **Step 1: Write the failing tests**

`tests/routing/test_policy.py`:

```python
import pytest

from phil.routing import CLASSES, DEPTH, Judgement, decide, parse_override


def judgement(task_class="simple_change", confidence=0.9, needs_detail=0.1, source="jev") -> Judgement:
    return Judgement(
        task_class=task_class, probabilities={task_class: 1.0}, confidence=confidence,
        needs_detail=needs_detail, source=source, latency_ms=10, usage=None,
    )


def test_every_class_has_a_depth_and_other_has_none():
    assert set(CLASSES) == set(DEPTH) | {"other"}
    assert DEPTH["question"] == DEPTH["diagnosis"] == "answer"
    assert {DEPTH[c] for c in ("small_operation", "simple_change", "focused_fix")} == {"quick"}
    assert {DEPTH[c] for c in ("feature", "refactor", "design", "broad_project")} == {"full"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/ask what does calc do", ("answer", "what does calc do")),
        ("/QUICK fix the typo", ("quick", "fix the typo")),
        ("/full   add auth", ("full", "add auth")),
        ("/ask", ("answer", "")),
        ("fix the typo", (None, "fix the typo")),
        ("/asking is not an override", (None, "/asking is not an override")),
        ("/btw hi", (None, "/btw hi")),
    ],
)
def test_parse_override(text, expected):
    assert parse_override(text) == expected


T = {"confidence_threshold": 0.5, "detail_threshold": 0.6}


@pytest.mark.parametrize(
    ("j", "expected"),
    [
        (None, (None, "unavailable")),
        (judgement(needs_detail=0.6), (None, "needs_detail")),  # the threshold is inclusive
        (judgement(needs_detail=0.59), ("quick", "class")),
        (judgement(task_class="other"), (None, "other")),
        (judgement(confidence=0.49), (None, "low_confidence")),
        (judgement(confidence=0.5), ("quick", "class")),
        (judgement(task_class="question"), ("answer", "class")),
        (judgement(task_class="feature"), ("full", "class")),
        # needs_detail is checked before confidence and class
        (judgement(task_class="question", confidence=0.2, needs_detail=0.9), (None, "needs_detail")),
    ],
)
def test_decide(j, expected):
    assert decide(j, **T) == expected
```

`tests/routing/test_types.py`:

```python
import subprocess

from phil.routing import route_state


def test_route_state_trims_chat_and_lists_files(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for i in range(70):
        (tmp_path / f"f{i:02d}.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    chat = [f"turn {i} " + "y" * 500 for i in range(6)]
    state = route_state("fix it", chat, tmp_path)
    assert state["request"] == "fix it"
    assert len(state["chat"]) == 4 and all(len(turn) <= 400 for turn in state["chat"])
    assert state["chat"][0].startswith("turn 2")  # the last four turns
    assert state["repo"]["test_cmd"] == "pytest"
    assert len(state["repo"]["files"]) == 60 and state["repo"]["file_count"] == 71


def test_route_state_outside_git_has_no_files(tmp_path):
    state = route_state("hi", [], tmp_path)
    assert state["repo"] == {"test_cmd": None, "files": [], "file_count": 0}
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/routing -q -n 0`
Expected: FAIL with `ModuleNotFoundError: phil.routing`.

- [ ] **Step 3: Implement**

`src/phil/routing/classes.py`:

```python
"""Task classes, their descriptions and examples, and the depth each maps to (spec §3.1).

The routing backends and the benchmark all read the wording from here, so the questions a model
sees are the same everywhere."""

from dataclasses import dataclass

DEPTHS = ("answer", "quick", "full")


@dataclass(frozen=True)
class ClassInfo:
    description: str
    examples: tuple[str, str]


CLASSES: dict[str, ClassInfo] = {
    "question": ClassInfo(
        "Asks for information or an explanation about the code or project; nothing should change.",
        ("How does the retry logic in invoke.py work?", "Which file defines the CLI commands?"),
    ),
    "diagnosis": ClassInfo(
        "Asks why something is broken or behaves unexpectedly, wanting the cause found, not yet fixed.",
        ("Why does the build fail on a clean checkout?", "Figure out why login sometimes returns 500."),
    ),
    "small_operation": ClassInfo(
        "A small mechanical operation with an obvious result: rename, bump a version, move or delete a file.",
        ("Bump the version to 1.4.2.", "Rename utils.py to helpers.py and update the imports."),
    ),
    "simple_change": ClassInfo(
        "A small, well-specified edit in one or two places: copy, markup, a config value, one small function.",
        ("Fix the typo 'recieve' in the footer.", "Add a meta description tag to the home page."),
    ),
    "focused_fix": ClassInfo(
        "Fix a specific, located bug whose cause is known or obvious from the request.",
        ("divide() crashes on zero; return None instead.", "The date parser drops the timezone; keep it."),
    ),
    "feature": ClassInfo(
        "Add new behaviour that needs design choices, several files, or new tests.",
        ("Add CSV export to the reports page.", "Support login with GitHub."),
    ),
    "refactor": ClassInfo(
        "Restructure existing code without changing behaviour, across more than a couple of places.",
        ("Split controller.py into smaller modules.", "Replace the hand-written retry loops with one helper."),
    ),
    "design": ClassInfo(
        "Decide or document an architecture or approach before (or instead of) building it.",
        ("Design a plugin system for Phil.", "How should we structure offline sync? Write it up."),
    ),
    "broad_project": ClassInfo(
        "Large, multi-part work spanning many files or subsystems.",
        ("Migrate the app from REST to GraphQL.", "Build a full admin dashboard with auth and audit logs."),
    ),
    "other": ClassInfo(
        "None of the above, or impossible to tell from the request.",
        ("Hello!", "Thanks, that's all."),
    ),
}

DEPTH: dict[str, str] = {
    "question": "answer",
    "diagnosis": "answer",
    "small_operation": "quick",
    "simple_change": "quick",
    "focused_fix": "quick",
    "feature": "full",
    "refactor": "full",
    "design": "full",
    "broad_project": "full",
}

TASK_CLASS_QUESTION = (
    "Which kind of work does `request` ask a coding agent to do in this repository? "
    "Use `chat` only to understand references like 'it' or 'that'."
)
NEEDS_DETAIL_QUESTION = (
    "Is `request` too ambiguous for a coding agent to act on without first asking the user something "
    "(a missing target, conflicting goals, or no way to tell what done means)?"
)
NEEDS_DETAIL_CRITERIA = {
    "true": "A competent developer would have to ask the user a question before starting.",
    "false": "A competent developer could start now, making reasonable choices on minor details.",
}
```

`src/phil/routing/types.py`:

```python
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from phil.repo_detect import detect_test_cmd

RouteState = dict[str, object]
CHAT_TURNS = 4
TURN_CHARS = 400
FILES = 60


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None


@dataclass(frozen=True)
class Judgement:
    task_class: str  # a CLASSES key
    probabilities: dict[str, float]  # one-hot for backends without a distribution
    confidence: float  # 0..1
    needs_detail: float  # 0..1, the probability the request must be clarified first
    source: Literal["jev", "llm", "fake"]
    latency_ms: int
    usage: Usage | None
    fallback_reason: str | None = None  # set on an llm judgement made because Jev failed


def _tracked_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in result.stdout.splitlines() if line] if result.returncode == 0 else []


def route_state(request: str, chat: list[str], root: Path) -> RouteState:
    """The `state` a routing question sees (spec §3.2): the request, the last few chat turns
    (trimmed), and what Phil already knows about the repo."""
    files = _tracked_files(root)
    return {
        "request": request,
        "chat": [turn[:TURN_CHARS] for turn in chat[-CHAT_TURNS:]],
        "repo": {"test_cmd": detect_test_cmd(root), "files": files[:FILES], "file_count": len(files)},
    }
```

`src/phil/routing/policy.py`:

```python
"""The routing policy (spec §3.5): plain code over a model's judgement, so thresholds can change
without new inference."""

from dataclasses import dataclass

from phil.routing.classes import DEPTH
from phil.routing.types import Judgement

OVERRIDES = {"/ask": "answer", "/quick": "quick", "/full": "full"}


@dataclass(frozen=True)
class Route:
    depth: str | None  # None: intake decides
    source: str  # "forced", "jev", "llm" or "intake"
    reason: str  # "forced", "class", "needs_detail", "other", "low_confidence" or "unavailable"
    text: str  # the message, without an override prefix
    judgement: Judgement | None


def parse_override(text: str) -> tuple[str | None, str]:
    """(forced depth, the rest of the message) for `/ask`, `/quick` or `/full` as the first word
    (any case); (None, text) otherwise."""
    head, _, rest = text.strip().partition(" ")
    depth = OVERRIDES.get(head.lower())
    if depth is None:
        return None, text
    return depth, rest.strip()


def decide(
    judgement: Judgement | None, *, confidence_threshold: float, detail_threshold: float
) -> tuple[str | None, str]:
    """(depth or None for "intake decides", reason), checked in order."""
    if judgement is None:
        return None, "unavailable"
    if judgement.needs_detail >= detail_threshold:
        return None, "needs_detail"
    if judgement.task_class not in DEPTH:
        return None, "other"
    if judgement.confidence < confidence_threshold:
        return None, "low_confidence"
    return DEPTH[judgement.task_class], "class"
```

`src/phil/routing/__init__.py` re-exports `CLASSES, DEPTH, DEPTHS, ClassInfo, TASK_CLASS_QUESTION, NEEDS_DETAIL_QUESTION, NEEDS_DETAIL_CRITERIA, Usage, Judgement, RouteState, route_state, Route, parse_override, decide, OVERRIDES`. Do not re-export `classify`: it imports agent code, and Task 4 adds it to `phil.routing.classify` only.

- [ ] **Step 4: Run them and confirm they pass**

Run: `uv run pytest tests/routing -q -n 0`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `Add the routing core: task classes, judgements and the policy`, plus the trailer.

---

### Task 3: The Jev backend

**Files:**
- Create: `src/phil/routing/jev.py`
- Test: `tests/routing/test_jev.py`, `tests/routing/fixtures/jev_ok.json`

**Interfaces:**
- Consumes: `CLASSES`, `TASK_CLASS_QUESTION`, `NEEDS_DETAIL_QUESTION`, `NEEDS_DETAIL_CRITERIA`, `Judgement`, `Usage`, `RouteState` (Task 2); `BUILTIN_PROVIDERS["typesafe"]` and `ProviderSpec` (Task 1).
- Produces:
  - `JevError(reason: str)`, where `.reason` is one of `"missing key"`, `"timeout"`, `"network"`, `"http 401"`, `"http 422"`, `"http 429"`, `"http 529"`, `"http <n>"` or `"malformed"`;
  - `build_request(model_name: str, state: RouteState) -> dict`;
  - `parse_response(body: object) -> tuple[str, dict[str, float], float, float, Usage | None]`;
  - `judge_jev(spec: ProviderSpec, model_name: str, state: RouteState, *, timeout_s: float, environ: Mapping[str, str] | None = None, transport: object | None = None, clock: Callable[[], float] = time.monotonic) -> Judgement`;
  - `ping_jev(spec, model_name, *, timeout_s, environ=None, transport=None) -> None`, which raises `JevError` (used by `models check` and setup).

- [ ] **Step 1: Write the fixture and the failing tests**

`tests/routing/fixtures/jev_ok.json`:

```json
{
  "model": "jev-latest",
  "answers": {
    "task_class": {
      "type": "choice",
      "choice": "simple_change",
      "probabilities": {"question": 0.02, "diagnosis": 0.01, "small_operation": 0.05, "simple_change": 0.86,
                        "focused_fix": 0.03, "feature": 0.01, "refactor": 0.005, "design": 0.005,
                        "broad_project": 0.005, "other": 0.005},
      "confidence": 0.81
    },
    "needs_detail": {"type": "noul", "noul": 0.07}
  },
  "usage": {"input_tokens": 412, "output_tokens": 3}
}
```

`tests/routing/test_jev.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from phil.agents.providers import BUILTIN_PROVIDERS
from phil.routing import CLASSES
from phil.routing.jev import JevError, build_request, judge_jev, ping_jev

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jev_ok.json").read_text())
SPEC = BUILTIN_PROVIDERS["typesafe"]
SECRET = "ts-TESTSECRET0123456789abcdefABCDEF"
ENV = {"TYPESAFE_API_KEY": SECRET}
STATE = {"request": "fix the typo", "chat": [], "repo": {"test_cmd": None, "files": [], "file_count": 0}}


def transport(handler):
    return httpx.MockTransport(handler)


def test_build_request_asks_both_questions():
    body = build_request("jev-latest", STATE)
    assert body["model"] == "jev-latest" and body["state"] == STATE
    task_class = body["questions"]["task_class"]
    assert task_class["type"] == "choice" and set(task_class["criteria"]) == set(CLASSES)
    assert task_class["criteria"]["question"]["examples"]  # descriptions carry examples
    needs = body["questions"]["needs_detail"]
    assert needs["type"] == "noul" and set(needs["criteria"]) == {"true", "false"}


def test_judge_jev_parses_a_good_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=FIXTURE)

    j = judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(handler))
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert (j.task_class, j.confidence, j.needs_detail, j.source) == ("simple_change", 0.81, 0.07, "jev")
    assert j.probabilities["simple_change"] == 0.86
    assert j.usage.input_tokens == 412 and j.usage.output_tokens == 3


@pytest.mark.parametrize(("status", "reason"), [(401, "http 401"), (422, "http 422"), (429, "http 429"),
                                                 (529, "http 529"), (500, "http 500")])
def test_http_errors_are_jev_errors_without_the_key(status, reason):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, text=f"nope {SECRET}")

    with pytest.raises(JevError) as info:
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(handler))
    assert info.value.reason == reason and SECRET not in str(info.value)
    assert calls == [1]  # exactly one attempt, no retries


def test_timeout_and_network_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(JevError, match="timeout"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(slow))
    with pytest.raises(JevError, match="network"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(down))


@pytest.mark.parametrize(
    "body",
    [
        {},  # no answers
        {"answers": {"needs_detail": {"noul": 0.1}}},  # no task_class
        {"answers": {"task_class": {"choice": "invent", "probabilities": {}, "confidence": 0.9},
                     "needs_detail": {"noul": 0.1}}},  # not one of the classes
        {"answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 2},
                     "needs_detail": {"noul": 0.1}}},  # out of range
        {"answers": {"task_class": {"choice": "question", "probabilities": {}, "confidence": 0.9}}},  # no noul
        "not json",
    ],
)
def test_malformed_bodies(body):
    def handler(request):
        return httpx.Response(200, json=body) if not isinstance(body, str) else httpx.Response(200, text=body)

    with pytest.raises(JevError, match="malformed"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ=ENV, transport=transport(handler))


def test_missing_key_never_calls_out():
    def handler(request):
        raise AssertionError("must not be called")

    with pytest.raises(JevError, match="missing key"):
        judge_jev(SPEC, "jev-latest", STATE, timeout_s=5, environ={}, transport=transport(handler))


def test_ping_sends_a_tiny_choice():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"answers": {"ping": {"type": "choice", "choice": "yes",
                                          "probabilities": {"yes": 1.0, "no": 0.0}, "confidence": 1.0}}})

    ping_jev(SPEC, "jev-latest", timeout_s=5, environ=ENV, transport=transport(handler))
    assert set(seen["body"]["questions"]) == {"ping"}
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/routing/test_jev.py -q -n 0`
Expected: FAIL with `ModuleNotFoundError: phil.routing.jev`.

- [ ] **Step 3: Implement `src/phil/routing/jev.py`**

```python
"""TypeSafe Jev over plain HTTP (spec §3.4): one attempt, no retries, the key never shown."""

import time
from collections.abc import Callable, Mapping
from typing import Any

from phil.agents.providers import ProviderSpec
from phil.key_store import key_lookup
from phil.routing.classes import (
    CLASSES,
    NEEDS_DETAIL_CRITERIA,
    NEEDS_DETAIL_QUESTION,
    TASK_CLASS_QUESTION,
)
from phil.routing.types import Judgement, RouteState, Usage


class JevError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_request(model_name: str, state: RouteState) -> dict:
    return {
        "model": model_name,
        "state": state,
        "questions": {
            "task_class": {
                "type": "choice",
                "instructions": TASK_CLASS_QUESTION,
                "criteria": {
                    key: {"description": info.description, "examples": list(info.examples)}
                    for key, info in CLASSES.items()
                },
            },
            "needs_detail": {
                "type": "noul",
                "instructions": NEEDS_DETAIL_QUESTION,
                "criteria": NEEDS_DETAIL_CRITERIA,
            },
        },
    }


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise JevError("malformed")
    return float(value)


def parse_response(body: object) -> tuple[str, dict[str, float], float, float, Usage | None]:
    """(choice, probabilities, confidence, needs_detail, usage); raises JevError("malformed")."""
    try:
        answers = body["answers"]  # type: ignore[index]
        task_class = answers["task_class"]
        choice = task_class["choice"]
        noul = answers["needs_detail"]["noul"]
        probabilities = task_class.get("probabilities") or {}
        confidence = task_class["confidence"]
    except (KeyError, TypeError):
        raise JevError("malformed") from None
    if choice not in CLASSES or not isinstance(probabilities, dict):
        raise JevError("malformed")
    probs = {str(k): _probability(v) for k, v in probabilities.items()}
    usage = None
    raw_usage = body.get("usage") if isinstance(body, dict) else None
    if isinstance(raw_usage, dict):
        usage = Usage(int(raw_usage.get("input_tokens", 0)), int(raw_usage.get("output_tokens", 0)))
    return choice, probs, _probability(confidence), _probability(noul), usage


def _post(
    spec: ProviderSpec, body: dict, *, timeout_s: float, environ: Mapping[str, str] | None, transport: object | None
) -> Any:
    import httpx

    env = environ if environ is not None else key_lookup()
    key = env.get(spec.api_key_env or "") or ""
    if not key:
        raise JevError("missing key")
    url = f"{(spec.base_url or '').rstrip('/')}/systemone"
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as client:  # type: ignore[arg-type]
            response = client.post(
                url, json=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            )
    except httpx.TimeoutException:
        raise JevError("timeout") from None
    except httpx.HTTPError:
        raise JevError("network") from None
    if response.status_code != 200:
        raise JevError(f"http {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise JevError("malformed") from None


def judge_jev(
    spec: ProviderSpec,
    model_name: str,
    state: RouteState,
    *,
    timeout_s: float,
    environ: Mapping[str, str] | None = None,
    transport: object | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Judgement:
    started = clock()
    body = _post(spec, build_request(model_name, state), timeout_s=timeout_s, environ=environ, transport=transport)
    choice, probabilities, confidence, needs_detail, usage = parse_response(body)
    return Judgement(
        task_class=choice, probabilities=probabilities, confidence=confidence, needs_detail=needs_detail,
        source="jev", latency_ms=int((clock() - started) * 1000), usage=usage,
    )


def ping_jev(
    spec: ProviderSpec,
    model_name: str,
    *,
    timeout_s: float,
    environ: Mapping[str, str] | None = None,
    transport: object | None = None,
) -> None:
    """One tiny request; raises JevError if Jev can't answer it."""
    body = {
        "model": model_name,
        "state": {"text": "ping"},
        "questions": {
            "ping": {"type": "choice", "instructions": "Is `text` the word ping?",
                     "criteria": {"yes": "It is.", "no": "It isn't."}}
        },
    }
    answer = _post(spec, body, timeout_s=timeout_s, environ=environ, transport=transport)
    try:
        if answer["answers"]["ping"]["choice"] not in ("yes", "no"):
            raise JevError("malformed")
    except (KeyError, TypeError):
        raise JevError("malformed") from None
```

`httpx` must not be imported at module level. It's imported inside `_post`, so `phil.routing.jev` stays light. The test file imports `httpx` itself, which is fine.

- [ ] **Step 4: Run them and confirm they pass**

Run: `uv run pytest tests/routing/test_jev.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `Add the Jev routing backend over plain HTTP`, plus the trailer.

---

### Task 4: The LLM backend and `classify()` with fallback

**Files:**
- Create: `src/phil/contracts/routing.py` (only `RouteInput` and `RouteJudgement` in this task), `src/phil/routing/llm.py`, `src/phil/routing/classify.py`, `src/phil/prompts/route.md`
- Modify: `src/phil/contracts/__init__.py` (export and add to `ALL_CONTRACTS`), `src/phil/agents/registry.py`
- Test: `tests/routing/test_classify.py`

**Interfaces:**
- Consumes: Tasks 1–3, `invoke_agent`, `AgentContext`, `build_packet`, `ScriptedAgentFactory`/`FakeAgentFactory` (`phil.agents.fake`).
- Produces:
  - `RouteInput(request: str, chat: list[str], repo: dict, classes: dict[str, str])`;
  - `RouteJudgement(task_class: Literal[<the ten keys>], confidence: float, needs_detail: float)`;
  - the `SPECS["route"]` spec: role `classifier`, lean harness, `end_on_text=True`, `shared_prompt=False`;
  - `judge_llm(ctx: AgentContext, state: RouteState, *, model: str | None = None, call: int = 1) -> Judgement`;
  - `classify(ctx: AgentContext, state: RouteState, *, call: int = 1, transport: object | None = None, environ: Mapping[str, str] | None = None) -> Judgement | None`.

- [ ] **Step 1: Write the failing tests**

`tests/routing/test_classify.py`:

```python
import httpx

from phil.agents.fake import FakeAgentFactory
from phil.agents.invoke import AgentContext
from phil.config import PhilConfig
from phil.contracts.routing import RouteJudgement
from phil.routing.classify import classify
from phil.store.db import connect
from tests.routing.test_jev import FIXTURE

STATE = {"request": "fix the typo", "chat": [], "repo": {"test_cmd": None, "files": [], "file_count": 0}}
LLM_SAYS = RouteJudgement(task_class="question", confidence=0.7, needs_detail=0.1)


def ctx(tmp_path, models, factory) -> AgentContext:
    return AgentContext(config=PhilConfig(models=models), conn=connect(tmp_path / "t.db"), layer="chat",
                        factory=factory)


def test_llm_backend_when_no_typesafe_classifier(tmp_path):
    factory = FakeAgentFactory([LLM_SAYS])
    j = classify(ctx(tmp_path, {"low": "openrouter:l", "high": "openrouter:h"}, factory), STATE)
    assert (j.source, j.task_class, j.confidence, j.probabilities) == ("llm", "question", 0.7, {"question": 1.0})


def test_jev_when_configured(tmp_path):
    models = {"low": "openrouter:l", "high": "openrouter:h", "classifier": "typesafe:jev-latest"}
    t = httpx.MockTransport(lambda r: httpx.Response(200, json=FIXTURE))
    j = classify(ctx(tmp_path, models, FakeAgentFactory([])), STATE, transport=t,
                 environ={"TYPESAFE_API_KEY": "k"})
    assert j.source == "jev" and j.task_class == "simple_change"


def test_jev_failure_falls_back_to_the_low_model(tmp_path):
    models = {"low": "openrouter:l", "high": "openrouter:h", "classifier": "typesafe:jev-latest"}
    t = httpx.MockTransport(lambda r: httpx.Response(429))
    factory = FakeAgentFactory([LLM_SAYS])
    j = classify(ctx(tmp_path, models, factory), STATE, transport=t, environ={"TYPESAFE_API_KEY": "k"})
    assert j.source == "llm" and j.fallback_reason == "http 429"
    assert factory.models == ["openrouter:l"]  # the fallback uses the low model, not typesafe


def test_llm_failure_returns_none(tmp_path):
    factory = FakeAgentFactory([RuntimeError("boom"), RuntimeError("boom")])
    assert classify(ctx(tmp_path, {"low": "openrouter:l", "high": "openrouter:h"}, factory), STATE) is None
```

If `FakeAgentFactory` doesn't record the models it was asked to build (`factory.models`), add a `models: list[str]` attribute that its `__call__` appends to. Read `src/phil/agents/fake.py` first and keep its existing behaviour. If it doesn't raise exceptions given as outputs, follow its existing pattern for scripted failures (see `tests/agents/test_scripted.py`) and adjust the last test to match.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/routing/test_classify.py -q -n 0`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`src/phil/contracts/routing.py`:

```python
from typing import Literal

from pydantic import Field

from phil.contracts.base import Contract

TaskClass = Literal[
    "question", "diagnosis", "small_operation", "simple_change", "focused_fix",
    "feature", "refactor", "design", "broad_project", "other",
]


class RouteInput(Contract):
    request: str
    chat: list[str] = []
    repo: dict = {}
    classes: dict[str, str] = Field(description="Each class key with its description and examples.")


class RouteJudgement(Contract):
    task_class: TaskClass = Field(description="The one class that best fits the request.")
    confidence: float = Field(ge=0, le=1, description="How sure you are of task_class, 0 to 1.")
    needs_detail: float = Field(
        ge=0, le=1, description="Probability (0 to 1) the user must be asked something before work can start."
    )
```

Add a test to `tests/routing/test_classify.py` asserting that `TaskClass`'s values equal `set(CLASSES)`, so the two lists can't drift apart.

`src/phil/prompts/route.md`:

```markdown
# Role: Router

Classify the user's `request` for a coding agent working in this repository. You do not answer or plan it.

- `task_class`: the one key from `classes` that fits best. Read each description and its examples. Use `chat` only to resolve references such as "it" or "that". Use `other` only when nothing fits.
- `confidence`: 0 to 1, how sure you are. Use lower values when two classes fit about equally.
- `needs_detail`: 0 to 1, the probability that a competent developer would have to ask the user something before starting (a missing target, conflicting goals, or no way to tell what done means).

Return only the structured output.
```

Add to `src/phil/agents/registry.py`:

```python
    "route": AgentSpec(
        "route", "classifier", RouteInput, RouteJudgement, harness="lean", shared_prompt=False, end_on_text=True
    ),
```

`src/phil/routing/llm.py`:

```python
import time

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts.routing import RouteInput, RouteJudgement
from phil.packets import build_packet
from phil.routing.classes import CLASSES
from phil.routing.types import Judgement, RouteState


def judge_llm(ctx: AgentContext, state: RouteState, *, model: str | None = None, call: int = 1) -> Judgement:
    classes = {key: f"{info.description} e.g. {info.examples[0]} / {info.examples[1]}" for key, info in CLASSES.items()}
    contract = RouteInput(
        request=str(state["request"]), chat=list(state.get("chat", [])), repo=dict(state.get("repo", {})),
        classes=classes,
    )
    packet = build_packet("classifier", contract, budget_tokens=ctx.config.budget_for("classifier").max_input_tokens)
    started = time.monotonic()
    out = invoke_agent(get_spec("route"), packet, ctx, node="route", call=call, model=model)
    assert isinstance(out, RouteJudgement)
    return Judgement(
        task_class=out.task_class, probabilities={out.task_class: 1.0}, confidence=out.confidence,
        needs_detail=out.needs_detail, source="llm", latency_ms=int((time.monotonic() - started) * 1000), usage=None,
    )
```

`src/phil/routing/classify.py`:

```python
"""Pick the routing backend and fall back (spec §3.4): Jev when the classifier is a systemone
model, else the classifier/low model; a Jev failure falls back to the low model; an LLM failure
returns None (intake decides)."""

import logging
from collections.abc import Mapping
from dataclasses import replace

from phil.agents.invoke import AgentContext
from phil.agents.providers import provider_for_model, split_model
from phil.routing.jev import JevError, judge_jev
from phil.routing.llm import judge_llm
from phil.routing.types import Judgement, RouteState

logger = logging.getLogger(__name__)


def classify(
    ctx: AgentContext,
    state: RouteState,
    *,
    call: int = 1,
    transport: object | None = None,
    environ: Mapping[str, str] | None = None,
) -> Judgement | None:
    config = ctx.config
    fallback_reason = None
    llm_model = None
    if config.is_systemone("classifier"):
        model = config.model_for("classifier")
        spec = provider_for_model(config, model, "classifier")
        try:
            return judge_jev(spec, split_model(model)[1], state, timeout_s=config.routing.jev_timeout_s,
                             environ=environ, transport=transport)
        except JevError as exc:
            fallback_reason = exc.reason
            llm_model = config.tier_model("low")
            if llm_model is None:
                return None
    try:
        judgement = judge_llm(ctx, state, model=llm_model, call=call)
    except Exception as exc:  # any model, contract or config failure: intake decides
        logger.info("routing llm backend failed: %s", type(exc).__name__)
        return None
    return replace(judgement, fallback_reason=fallback_reason) if fallback_reason else judgement
```

The fallback `low` model doesn't go through `model_for("classifier")`, which would return the typesafe model. It's passed as `model=` to `invoke_agent`, which pins it.

- [ ] **Step 4: Run them and confirm they pass**

Run: `uv run pytest tests/routing -q -n 0`, then `uv run pytest -q`.
Expected: PASS. `tests/test_contracts.py` may enumerate `ALL_CONTRACTS`, so add `RouteInput` and `RouteJudgement` there.

- [ ] **Step 5: Commit**

Message: `Add the LLM routing backend and classify() with Jev fallback`, plus the trailer.

---

### Task 5: The light harness and the read-only `answerer`

**Files:**
- Modify: `src/phil/agents/spec.py`, `src/phil/agents/factory.py`, `src/phil/agents/invoke.py`, `src/phil/agents/registry.py`, `src/phil/contracts/routing.py`, `src/phil/contracts/__init__.py`
- Create: `src/phil/prompts/answer.md`, `src/phil/chat/answer.py`
- Test: `tests/agents/test_light_harness.py`, `tests/chat/test_answer.py`

**Interfaces:**
- Produces:
  - `AgentSpec.harness: Literal["deep", "lean", "light"]`; `AgentSpec.read_only_shell: bool = False`; `AgentSpec.max_model_calls: int | None = None`;
  - `READ_TOOLS = ("ls", "read_file", "glob", "grep")` in `factory.py`;
  - `AnswerInput(question: str, context: str = "", repo_overview: str = "")`;
  - `Answer(text: str, files: list[str])`;
  - `SPECS["answer"]`: role `answerer`, harness `light`, `tools=("shell",)`, `read_only_shell=True`, `max_model_calls=12`;
  - `ANSWER_MAX_MODEL_CALLS = 12` (in `phil.chat.answer`);
  - `ask_answer(ctx: AgentContext, question: str, *, root: Path, overview: str, context: str = "", call: int = 1) -> Answer`.

- [ ] **Step 1: Check the APIs this task relies on**

Run `npx ctx7@latest library LangChain "create_agent middleware wrap_model_call"`, then `docs` on the best match. Confirm:
- `langchain.agents.create_agent(model, tools=..., system_prompt=..., response_format=..., middleware=[...])`;
- that `AgentMiddleware.wrap_model_call(self, request, handler)` exists, and that `request.override(tools=..., system_prompt=...)` (or the equivalent in the installed version) is available.

Then read `.venv/lib/python3.14/site-packages/deepagents/middleware/filesystem.py` around `class FilesystemMiddleware`. Confirm that `FilesystemMiddleware(backend=..., tools=[...])` limits the tools to the names given. If either API differs, keep the behaviour in Step 3 and adapt the calls. Report the difference as DONE_WITH_CONCERNS.

- [ ] **Step 2: Write the failing tests**

`tests/agents/test_light_harness.py`:

```python
from phil.agents.factory import READ_TOOLS, _call_budget_middleware, build_agent
from phil.agents.registry import get_spec
from phil.agents.spec import AgentSpec


def test_answer_spec_is_light_read_only_and_capped():
    spec = get_spec("answer")
    assert (spec.harness, spec.role, spec.read_only_shell, spec.max_model_calls) == ("light", "answerer", True, 12)
    assert spec.writes_files is False and spec.tools == ("shell",)


def test_light_agent_has_only_read_tools_and_no_task_tool(tmp_path, monkeypatch):
    # Build with a fake chat model so no provider or network is involved.
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    monkeypatch.setattr("phil.agents.factory.chat_model", lambda *a, **k: GenericFakeChatModel(messages=iter([])))
    agent = build_agent(get_spec("answer"), "openrouter:x", tmp_path, [])
    assert set(READ_TOOLS) <= _tool_names(agent)
    assert not {"write_file", "edit_file", "task", "execute"} & _tool_names(agent)


def _tool_names(agent) -> set[str]:
    tools_node = agent.nodes.get("tools")
    bound = getattr(getattr(tools_node, "bound", None), "tools_by_name", {}) if tools_node else {}
    return set(bound)


def test_call_budget_forces_an_answer_on_the_last_call():
    mw = _call_budget_middleware(3)
    seen = []

    class Req:
        def __init__(self, n):
            self.state = {"messages": [type("AI", (), {"type": "ai"})()] * n}
            self.tools = ["ls", "read_file"]
            self.system_prompt = "base"

        def override(self, **kw):
            r = Req(len(self.state["messages"]))
            r.tools, r.system_prompt = kw.get("tools", self.tools), kw.get("system_prompt", self.system_prompt)
            return r

    for n in (0, 1, 2):
        mw.wrap_model_call(Req(n), lambda r: seen.append((r.tools, r.system_prompt)))
    assert seen[0][0] == ["ls", "read_file"] and seen[1][0] == ["ls", "read_file"]
    assert seen[2][0] == [] and "Answer now" in seen[2][1]
```

These tests assert behaviour, not LangChain internals. If `_tool_names` can't see the tools through the compiled graph in the installed version, replace it with a check on the `tools` argument `create_agent` received: monkeypatch `langchain.agents.create_agent` to capture its kwargs and the `FilesystemMiddleware` instance's tool names. Keep both assertions.

`tests/chat/test_answer.py`:

```python
from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.agents.invoke import AgentContext
from phil.agents.tools import CommandLog
from phil.chat.answer import ask_answer
from phil.config import PhilConfig
from phil.contracts.routing import Answer
from phil.store.db import connect


def test_ask_answer_returns_the_answer(tmp_path):
    factory = ScriptedAgentFactory({"answer": [Answer(text="calc.add sums two ints.", files=["calc.py"])]})
    ctx = AgentContext(config=PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"}),
                       conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)
    answer = ask_answer(ctx, "what does add do?", root=tmp_path, overview="Tracked files:\ncalc.py")
    assert answer.files == ["calc.py"]


def test_read_only_shell_denies_project_commands_and_writes(tmp_path):
    from phil.agents.invoke import _shell_for

    config = PhilConfig(models={"low": "openrouter:l"})
    run = _shell_for(get_spec_answer(), config, tmp_path, CommandLog(), extra_allow=("npm test",), approved=("rm x",))
    assert run("ls").startswith("exit_code: 0")
    assert run("pytest").startswith("DENIED")  # on the default project allowlist, but not for the answerer
    assert run("npm test").startswith("DENIED")  # extra_allow ignored
    assert run("rm x").startswith("DENIED")  # approvals ignored
    assert run("touch y").startswith("DENIED")


def get_spec_answer():
    from phil.agents.registry import get_spec

    return get_spec("answer")
```

`_shell_for` is a small helper extracted from `invoke_agent` in Step 3, so the read-only policy can be tested without running an agent.

- [ ] **Step 3: Run them and confirm they fail**

Run: `uv run pytest tests/agents/test_light_harness.py tests/chat/test_answer.py -q -n 0`
Expected: FAIL.

- [ ] **Step 4: Implement**

`spec.py`: extend `harness` to `Literal["deep", "lean", "light"]` and add these fields, with comments:

```python
    # "light": a plain LangChain agent with deepagents' file tools (read-only for writes_files=False)
    # and Phil's shell tool, but no sub-agent and no summarization: for short, bounded jobs.
    read_only_shell: bool = False  # shell runs only M1's read-only commands (no project allowlist)
    max_model_calls: int | None = None  # light only: the last allowed call must answer
```

`factory.py`:

```python
READ_TOOLS = ("ls", "read_file", "glob", "grep")
ANSWER_NOW = "Your tool budget is used up. Answer now with what you have found, and say what you didn't check."


def _call_budget_middleware(max_calls: int) -> Any:
    """On the last allowed model call, remove the tools and tell the model to answer, so a
    capped agent still returns its structured output instead of hitting a recursion limit."""
    from langchain.agents.middleware import AgentMiddleware

    class CallBudget(AgentMiddleware):
        def wrap_model_call(self, request: Any, handler: Any) -> Any:
            calls = sum(1 for m in request.state["messages"] if getattr(m, "type", None) == "ai")
            if calls >= max_calls - 1:
                request = request.override(tools=[], system_prompt=f"{request.system_prompt}\n\n{ANSWER_NOW}")
            return handler(request)

    return CallBudget()


def _build_light_agent(
    spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]], timeout_s: int,
    provider: ProviderSpec | None,
) -> Any:
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents import create_agent

    from phil.agents.model_retry import PhilModelRetryMiddleware

    if workdir is None:
        raise ValueError(f"{spec.name} uses the light harness, which needs a workdir")
    fs_tools = list(READ_TOOLS) if not spec.writes_files else "all"
    middleware: list[Any] = [
        FilesystemMiddleware(backend=FilesystemBackend(root_dir=workdir, virtual_mode=True), tools=fs_tools),
        PhilModelRetryMiddleware(),
    ]
    if spec.max_model_calls is not None:
        middleware.append(_call_budget_middleware(spec.max_model_calls))
    return create_agent(
        chat_model(model, timeout_s, provider=provider, used_by=(spec.role,)),
        tools=tools,
        system_prompt=load_prompt(spec),
        response_format=_tool_strategy(spec),
        middleware=middleware,
    )
```

In `build_agent`, route `spec.harness == "light"` to `_build_light_agent`. `writes_files=True` light agents are M3b's concern. For now, raise `ValueError("light harness with writes_files is not supported yet")` if `spec.writes_files`, because the write tools would need `filesystem_permissions` (`.git` and `phil.toml` denied), and M3b adds them through `FilesystemMiddleware(_permissions=...)`.

`invoke.py`: extract the shell-tool construction into a helper and use it in `invoke_agent`:

```python
def _shell_for(
    spec: AgentSpec, config: PhilConfig, workdir: Path, log: CommandLog, *,
    artifacts: ArtifactStore | None = None, log_prefix: str = "",
    extra_allow: tuple[str, ...] = (), approved: tuple[str, ...] = (),
) -> Callable[[str], str]:
    """The shell tool for `spec`. A read-only spec gets no project allowlist, plan commands or
    approvals: only M1's read-only commands run."""
    shell = config.shell
    if spec.read_only_shell:
        shell, extra_allow, approved = shell.model_copy(update={"allow": []}), (), ()
    return make_shell_tool(workdir, shell, log, artifacts, log_prefix=log_prefix,
                           extra_allow=extra_allow, approved=approved)
```

`contracts/routing.py`: add

```python
class AnswerInput(Contract):
    question: str
    context: str = ""  # e.g. an earlier diagnosis, or the last few chat turns
    repo_overview: str = ""


class Answer(Contract):
    text: str = Field(description="The answer, in plain prose; short unless the question needs more.")
    files: list[str] = Field(default=[], description="Repo-relative files the answer relies on.")
```

Export both and add them to `ALL_CONTRACTS`.

`prompts/answer.md`:

```markdown
# Role: Answerer

The user asked a question about this repository, or asked why something is broken. Answer it. You never change anything.

## Tools
- `ls`, `read_file`, `glob`, `grep` on the repository, and a shell that runs only read-only commands (`git log`, `git diff`, `grep`, `cat`, ...). Running tests or builds is not available.
- Read only what you need. Start from `repo_overview` to find likely files.

## Answer
- `text`: answer the question directly, citing the code you read. For "why is X broken", give the most likely cause and the evidence; say what you couldn't confirm.
- `files`: the repo-relative files your answer relies on.
- Never invent file contents or results. If you can't find it, say so.
```

Add `"answer"` to the registry. Use `shared_prompt=False`, because `_shared.md`'s `self_check` doesn't apply to `Answer`.

`chat/answer.py`:

```python
from dataclasses import replace
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts.routing import Answer, AnswerInput
from phil.packets import build_packet

ANSWER_MAX_MODEL_CALLS = 12


def ask_answer(ctx: AgentContext, question: str, *, root: Path, overview: str, context: str = "", call: int = 1) -> Answer:
    contract = AnswerInput(question=question, context=context, repo_overview=overview)
    packet = build_packet("answerer", contract, budget_tokens=ctx.config.budget_for("answerer").max_input_tokens)
    out = invoke_agent(get_spec("answer"), packet, replace(ctx, workdir=root), node="answer", call=call)
    assert isinstance(out, Answer)
    return out
```

The registry spec uses `max_model_calls=ANSWER_MAX_MODEL_CALLS`. Define the constant in `phil.agents.registry`, and have `phil.chat.answer` import it from there, so there's one source.

- [ ] **Step 5: Run them and confirm they pass**

Run: `uv run pytest tests/agents tests/chat/test_answer.py -q -n 0`, then `uv run pytest -q`.
Expected: PASS.

- [ ] **Step 6: Commit**

Message: `Add the light harness and the read-only answerer`, plus the trailer.

---

### Task 6: Intake decides depth when routing can't

**Files:**
- Modify: `src/phil/contracts/interface.py` (`Goal`), `src/phil/prompts/intake.md`
- Test: `tests/chat/test_intake_spec.py` (add)

**Interfaces:**
- Produces: `Goal.depth: Literal["answer", "quick", "full"] | None = None`.

- [ ] **Step 1: Write the failing test**

```python
from phil.contracts import Goal


def test_goal_depth_is_optional_and_typed():
    assert Goal(objective="x").depth is None
    assert Goal(objective="x", depth="answer").depth == "answer"
    import pytest
    with pytest.raises(ValueError):
        Goal(objective="x", depth="medium")
```

Also add an assertion that `"depth"` appears in `intake.md`'s text, next to the other prompt checks in that file.

- [ ] **Step 2: Run it and confirm it fails.** Run: `uv run pytest tests/chat/test_intake_spec.py -q -n 0`

- [ ] **Step 3: Implement.**
  - Add the field to `Goal` with `Field(default=None, description=...)`.
  - Add this to `intake.md`, under "Goal fields":

```markdown
- `depth`: how much process the work needs: `answer` (a question or a "why is X broken" diagnosis, no change), `quick` (one small, well-specified change), or `full` (anything needing design, several files, or a plan). Leave null while `open_questions` is non-empty.
```

- [ ] **Step 4: Run it and confirm it passes.** Then run the full suite.

- [ ] **Step 5: Commit.** Message: `Let intake choose a depth when routing defers to it`, plus the trailer.

---

### Task 7: Route every chat message

**Files:**
- Modify: `src/phil/chat/controller.py`
- Create: `src/phil/ui/answer_view.py`
- Test: `tests/chat/test_routing_flow.py`; update `tests/chat/chat_scenarios.py` / `tests/chat/conftest.py` fixtures as needed

**Interfaces:**
- Consumes:
  - `classify` (Task 4);
  - `route_state`, `parse_override`, `decide`, `Route` (Task 2);
  - `ask_answer`, `Answer` (Task 5);
  - `Goal.depth` (Task 6).
- Produces: chat stages `"routing"`, `"answering"` and `"confirm_fix"`; the transcript notes `route` and `answer`; `render_answer(console, answer)`.

**Behaviour** (spec §3.5, §3.6, §3.8). Implement exactly this:

1. **`_input`:** before the `/` command branch, call `parse_override(text)`. If it returns a depth:
   - an empty rest prints `Usage: /ask|/quick|/full <message>` and returns;
   - otherwise the input is treated as goal text, recorded with stage `goal`, with `forced=<depth>`. In `idle` it starts a goal. In a goal-job stage it goes through the same confirm-replace flow as a typed goal, keeping the forced depth for the replacement.
2. **`_begin_goal(text, forced=None)`:** after the existing reset, if `forced` is set, record `Route(forced, "forced", "forced", text, None)` and go to step 4. Otherwise set stage `"routing"`, step `"routing"`, and submit a job running `classify(ctx, route_state(text, self._recent, self.info.root), call=n)`. The result is posted as `route_ready` with `{"judgement": j, "text": text}`.
3. **`_on_route_ready`:**

   ```
   decide(j, confidence_threshold=self.config.routing.confidence_threshold,
          detail_threshold=self.config.routing.detail_threshold)
   ```

   This builds the `Route`. Its `source` is `j.source` when the depth is set, else `"intake"`. If `j.fallback_reason` is set, first print, dim, `Router unavailable ({reason}); using your low model.`. Then:
   - **depth `answer`:** start the answer job (step 5).
   - **depth `quick` or `full`, or `None`:** print the status line, then call the existing `_intake_job(text)`, so intake writes the goal and questions.
4. **`_on_goal_ready`:** when the route's depth was `None` (intake decides) and `goal.depth == "answer"` with no open questions, start the answer job instead of planning. The existing question rounds are unchanged.
5. **Answer job:**
   - stage `"answering"`, step `"answering"`;
   - the job runs `ask_answer(ctx, text, root=self.info.root, overview=self.overview, context=<the last 4 _recent turns joined>)` and posts `answer_ready`;
   - **`_on_answer_ready`:**
     - record the note `answer` (`text`, `files`, `task_class`);
     - render it with `render_answer`;
     - append `f"phil: {answer.text[:400]}"` to `_recent`;
     - for class `diagnosis`, set stage `"confirm_fix"`, with prompt `Fix it? [Enter = plan the fix / n] › `;
     - otherwise set stage `"idle"`.
   - **Failure** (`answer_failed`): print `Phil couldn't answer that: <error>` and return to `idle`.
6. **`confirm_fix`:**
   - Enter, `y` or `yes` calls `_begin_goal(f"{original}\n\nDiagnosis so far:\n{answer.text}", forced="quick")`;
   - `n` or `no` returns to `idle`;
   - anything else starts a new goal with that text (the normal `idle` path).

   Ctrl-C returns to `idle`. Add `"confirm_fix"` to `_interrupt`.
7. **Status lines** (dim, one line, printed once per message). Use `CLASS_LABELS = {"question": "Question", "diagnosis": "Diagnosis", "small_operation": "Small operation", "simple_change": "Simple change", "focused_fix": "Focused fix", "feature": "Feature", "refactor": "Refactor", "design": "Design", "broad_project": "Broad project", "other": "Unclear"}` in `controller.py`.

   | Situation | Status line |
   |---|---|
   | answer | `{label} · answering (/full to plan a change instead)` |
   | quick | `{label} · planning  (/quick and /full force a path)`. M3a only; M3b changes it to "quick path". |
   | full | `{label} · full plan` |
   | forced | `Forced: {depth} path` |
   | `needs_detail` | `Unclear request · asking first` |
   | low confidence, `other` or unavailable | `Not sure how big this is · intake decides` |

8. **`_recent: collections.deque[str]`** (maxlen 8):
   - every goal text is appended as `f"you: {text[:400]}"`;
   - every answer as shown in step 5;
   - every plan approval as `f"phil: planned {plan.keyword}"`.
9. **Records:**
   - the `route` note: `{task_class, depth, source, reason, confidence, needs_detail, latency_ms, fallback_reason}`. The values are `None` when forced.
   - the run's depth: pass `depth` in the decision dict given to `prepare_run`, and store it with the run. If `prepare_run` has no field for it, add `depth: str | None = None` to the run record (`phil.store.runs`) with a migration-free default, and show it in `phil show`. Read `src/phil/store/runs.py` first. If adding a column needs a schema migration, record the depth only in the chat's `route` note and say so in your report (DONE_WITH_CONCERNS).
10. **Stages:**
    - add `"routing"` and `"answering"` to `GOAL_JOB_STAGES` (so typing during them triggers confirm-replace, and Ctrl-C cancels them through `_next_generation`);
    - add `"confirm_fix"` to `PROMPTS` (the prompt above);
    - add all three to `TRANSCRIPT_STAGES`, mapped to `"goal"`;
    - update `HELP`: `Type a goal or a question. Prefix with /ask, /quick or /full to choose the path.`, followed by the existing command list.

`src/phil/ui/answer_view.py`:

```python
from rich.console import Console
from rich.markup import escape

from phil.contracts.routing import Answer


def render_answer(console: Console, answer: Answer) -> None:
    console.print(escape(answer.text))
    if answer.files:
        console.print(f"[phil.muted]Files: {escape(', '.join(answer.files))}[/]")
```

- [ ] **Step 1: Write the failing tests**

In `tests/chat/test_routing_flow.py`, use the existing chat harness: the `ChatController` driven by scripted inputs, a `ScriptedAgentFactory` keyed by spec name (`"route"`, `"intake"`, `"answer"`, `"architect"`, `"critic"`), and `io.submit` inline. Read `tests/chat/chat_scenarios.py` and `tests/chat/test_controller.py` to reuse their helpers. Cover:

```python
# Each test drives one or more inputs and asserts on console output, stages, notes and factory.remaining().

def test_question_is_answered_without_intake_or_planning(...):
    # route -> RouteJudgement(question, 0.9, 0.1); answer -> Answer("It sums.", ["calc.py"])
    # assert "Question · answering" in output, "It sums." in output, stage == "idle",
    # factory.remaining() shows intake/architect/critic unused, no run spawned.

def test_simple_change_goes_to_intake_and_planning_with_status(...):
    # route -> simple_change 0.9; then the existing intake/architect/critic scripts
    # assert "Simple change · planning" in output and the plan is shown for approval.

def test_low_confidence_lets_intake_decide_and_intake_can_choose_answer(...):
    # route -> feature 0.3; intake -> Goal(objective=..., depth="answer"); answer -> Answer(...)
    # assert "Not sure how big this is · intake decides" and the answer is rendered; no architect call.

def test_needs_detail_asks_first(...):
    # route -> simple_change 0.9 needs_detail 0.8; intake -> Goal(open_questions=["Which page?"])
    # assert "Unclear request · asking first" and the question is printed, stage == "questions".

def test_forced_prefix_skips_the_router(...):
    # input "/full add auth": no "route" script consumed; "Forced: full path" printed; intake runs.

def test_bare_override_prints_usage(...):
    # input "/ask": output contains "Usage: /ask|/quick|/full <message>", stage stays idle, no job.

def test_diagnosis_offers_a_fix_and_enter_plans_it(...):
    # route -> diagnosis 0.9; answer -> Answer("The env var is unset.", [...]); then input "" (Enter)
    # assert prompt "Fix it? [Enter = plan the fix / n] › " shown; then intake receives a message
    # containing "Diagnosis so far:" and "The env var is unset."

def test_router_unavailable_line_when_jev_fails(...):
    # config with classifier typesafe:jev-latest; monkeypatch phil.chat.controller.classify to return a
    # judgement with fallback_reason="http 429"; assert "Router unavailable (http 429); using your low model."

def test_route_note_is_recorded(...):
    # after a routed message, the session transcript has a "route" note with task_class, depth, source.

def test_new_goal_while_routing_asks_to_replace(...):
    # submit that defers the routing job; type a second message; stage == "confirm_replace"; "y" replaces;
    # the first route_ready (old generation) is dropped.
```

Write these as full tests with the repo's real fixtures. Do not leave the comments as the test body. Monkeypatch `phil.chat.controller.classify` only in the Jev-failure test. Every other test goes through the real `classify` with a `ScriptedAgentFactory` `"route"` script, and the test config has no typesafe classifier, so the `llm` backend runs.

- [ ] **Step 2: Run them and confirm they fail.** Run: `uv run pytest tests/chat/test_routing_flow.py -q -n 0`

- [ ] **Step 3: Implement** the behaviour above in `controller.py`.
  - Keep the new code in small methods: `_route_job`, `_on_route_ready`, `_status_line`, `_answer_job`, `_on_answer_ready`, `_on_answer_failed`, `_confirm_fix`.
  - Existing chat tests that type a goal now need a `"route"` script entry first. Update `tests/chat/conftest.py` or `chat_scenarios.py` so the shared scripted factory prepends `RouteJudgement(task_class="feature", confidence=0.9, needs_detail=0.1)` by default. That keeps their behaviour (full plan) unchanged. Don't edit each test separately where a shared helper can do it.

- [ ] **Step 4: Run it and confirm it passes.**
  - Run: `uv run pytest tests/chat -q`, then `uv run pytest -q`.
  - Then drive the chat by hand with a fake factory if the repo has a demo path (check `tests/chat/test_terminal.py`). Otherwise rely on the scripted tests.

- [ ] **Step 5: Commit.** Message: `Route every chat message: answer, quick or full`, plus the trailer.

---

### Task 8: `models check` and setup for the classifier

**Files:**
- Modify: `src/phil/agents/check.py`, `src/phil/setup/flow.py`
- Test: `tests/agents/test_check.py`, `tests/setup/test_flow.py`

**Interfaces:**
- Consumes: `ping_jev`, `JevError` (Task 3); `BUILTIN_PROVIDERS["typesafe"]`; `_key_step`, `_Provider` and `write_global_config` (existing).
- Produces:
  - `check_models(..., jev_transport: object | None = None)`;
  - `run_setup(..., classifier_check: Callable[[], str | None] | None = None)`, which returns `None` when ok, or the error reason.

- [ ] **Step 1: Write the failing tests**

`tests/agents/test_check.py` (add):

```python
import httpx

from phil.agents.check import check_models
from phil.config import PhilConfig


def test_check_pings_a_typesafe_classifier(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    ok = {"answers": {"ping": {"type": "choice", "choice": "yes", "probabilities": {"yes": 1.0}, "confidence": 1.0}}}
    config = PhilConfig(models={"classifier": "typesafe:jev-latest"})
    results = check_models(config, repo_root=tmp_path,
                           jev_transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ok)))
    [result] = [r for r in results if r.model == "typesafe:jev-latest"]
    assert result.ok and result.label == "classifier"


def test_check_reports_a_failing_typesafe_classifier_plainly(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    config = PhilConfig(models={"classifier": "typesafe:jev-latest"})
    results = check_models(config, repo_root=tmp_path,
                           jev_transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    [result] = [r for r in results if r.model == "typesafe:jev-latest"]
    assert not result.ok and result.detail == "TypeSafe refused the request (http 401): check TYPESAFE_API_KEY."
```

`tests/setup/test_flow.py` (add), using the existing `ScriptedSetupIO`. Read the file's helpers first.

- Choosing **"Your low model"** at the classifier step writes no `classifier` key.
- Choosing **"TypeSafe Jev"**:
  - asks for the key at a hidden prompt (secret scripted);
  - runs `classifier_check` (a fake returning `None`);
  - writes `models.classifier = "typesafe:jev-latest"` alongside `high` and `low`.
- A failing `classifier_check` (returning `"http 401"`) offers `["Use your low model instead", "Keep TypeSafe Jev anyway"]`. The first writes no `classifier` key.
- A rerun where the global file already sets `models.classifier` offers `Keep <model>` as the default first option, and keeping it writes nothing new for `classifier`.
- A key pasted at the classifier choice prompt is handled by the existing `choose` (an index prompt), so no new guard is needed. Assert that the planted secret never appears in `io.lines`.

- [ ] **Step 2: Run them and confirm they fail.**

- [ ] **Step 3: Implement**

`check.py`:
- `check_targets` already includes the `classifier` role. For a typesafe classifier its label is `"classifier"`; with no classifier set, the classifier resolves to `low` and shares that label.
- In `_check_one`, before the `invoke_agent` path:

```python
        if provider.kind == SYSTEMONE:
            try:
                ping_jev(provider, split_model(model)[1], timeout_s=ctx.config.routing.jev_timeout_s,
                         transport=jev_transport)
            except JevError as exc:
                return False, _jev_detail(provider, exc)
            return True, ""
```

with:

```python
def _jev_detail(provider: ProviderSpec, exc: JevError) -> str:
    if exc.reason == "missing key":
        return missing_key_message(provider.name, provider.api_key_env or "", ["classifier"])
    if exc.reason in ("http 401", "http 403"):
        return f"TypeSafe refused the request ({exc.reason}): check {provider.api_key_env}."
    return f"TypeSafe didn't answer ({exc.reason})."
```

Thread `jev_transport` through `check_models` into `_check_one`.

`setup/flow.py`: add the step `_classifier_step(io, config, target)` after `_check_step`, returning `dict[str, str]` (empty, or `{"classifier": "typesafe:jev-latest"}`).

```python
CLASSIFIER_MODEL = "typesafe:jev-latest"


def _classifier_step(io, config, target, classifier_check) -> dict[str, str]:
    current = config.models.get("classifier") if config.sources.get("models.classifier") == str(target) else None
    options = ([f"Keep {current}"] if current else []) + [
        "Your low model (default)", "TypeSafe Jev (fast routing; needs TYPESAFE_API_KEY)",
    ]
    choice = options[io.choose("Route requests with a fast classifier?", options)]
    if choice.startswith("Keep ") or choice.startswith("Your low model"):
        if current and choice.startswith("Your low model"):
            io.say(f"models.classifier = {current} stays in {target}; remove it there to route with your low model.")
        return {}
    _key_step(io, _Provider("typesafe", "TYPESAFE_API_KEY"))
    reason = classifier_check()
    if reason is not None:
        io.say(f"✗ classifier  {CLASSIFIER_MODEL}  {reason}")
        if io.choose("TypeSafe Jev failed the check.",
                     ["Use your low model instead", "Keep TypeSafe Jev anyway"]) == 0:
            return {}
    else:
        io.say(f"✓ classifier  {CLASSIFIER_MODEL}")
    return {"classifier": CLASSIFIER_MODEL}
```

- In `run_setup`, call it inside the `try` after `_check_step`, and merge the result into `models` before `write`. The summary line then lists `models.classifier` when written.
- The default `classifier_check` builds the provider with `resolve_provider(config, "typesafe")` and calls `ping_jev`. It returns `None`, or the `JevError.reason`.
- `_summarise` prints `Wrote models.high = …, models.low = …` as today, plus ` and models.classifier = …` when present. Update its existing f-string, and the tests that assert it.

- [ ] **Step 4: Run them and confirm they pass.** Run: `uv run pytest tests/agents/test_check.py tests/setup -q -n 0`, then `uv run pytest -q`.

- [ ] **Step 5: Commit.** Message: `Check and set up a TypeSafe classifier`, plus the trailer.

---

### Task 9: The classifier benchmark and docs

**Files:**
- Create:
  - `tests/live/bench/classify/__init__.py`
  - `tests/live/bench/classify/cases.jsonl`
  - `tests/live/bench/classify/metrics.py`
  - `tests/live/bench/classify/run.py`
  - `tests/live/bench/classify/test_classify_bench.py`
  - `tests/live/bench/classify/test_metrics_offline.py`
  - `docs/superpowers/plans/2026-09-30-phil-m3a-followups.md`
- Modify: `README.md`, `docs/superpowers/roadmap.md`

**Interfaces:**
- Consumes: `judge_jev`, `judge_llm`, `decide`, `DEPTH` and `CLASSES`.
- Produces:
  - `metrics.summarise(records: list[dict], *, confidence_threshold: float, detail_threshold: float) -> dict`;
  - `metrics.sweep(records, thresholds=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)) -> list[dict]`;
  - `python -m tests.live.bench.classify.run --report`.

**Cases file** (`cases.jsonl`): one JSON object per line, `{"id", "request", "chat": [], "repo": {"test_cmd", "files", "file_count"}, "expected_class", "expected_depth", "ambiguous": bool}`. Write **40** cases:

| Count | Kind | `expected_class` / `expected_depth` / `ambiguous` |
|---|---|---|
| 6 | question | `question` / `answer` |
| 4 | diagnosis | `diagnosis` / `answer` |
| 4 | small_operation | `small_operation` / `quick` |
| 6 | simple_change | `simple_change` / `quick` |
| 4 | focused_fix | `focused_fix` / `quick` |
| 4 | feature | `feature` / `full` |
| 3 | refactor | `refactor` / `full` |
| 2 | design | `design` / `full` |
| 1 | broad_project | `broad_project` / `full` |
| 6 | vague | `ambiguous: true`; `expected_depth` is the depth after clarification; `expected_class` is the most likely class |

Rules:
- Vary the length: 8 requests over 300 characters, some under 6 words.
- Vary the register: terse imperatives, polite questions, and requests that mix a question with a change (classed by the change).
- Use three repo contexts: the `py-calc` and `static-site` fixtures' real file lists, and one made-up web app with about 40 files.
- Two cases use `chat` to resolve "it" or "that".

The first five lines, verbatim:

```json
{"id": "q-01", "request": "What does calc.divide return when the divisor is zero?", "chat": [], "repo": {"test_cmd": "pytest", "files": ["calc.py", "tests/test_calc.py", "pyproject.toml"], "file_count": 3}, "expected_class": "question", "expected_depth": "answer", "ambiguous": false}
{"id": "d-01", "request": "The site build started failing after my last commit and I can't see why. Can you find out what's wrong?", "chat": [], "repo": {"test_cmd": null, "files": ["build.mjs", "src/index.html", "src/about.html", "package.json"], "file_count": 4}, "expected_class": "diagnosis", "expected_depth": "answer", "ambiguous": false}
{"id": "s-01", "request": "fix the typo recieve -> receive in the footer", "chat": [], "repo": {"test_cmd": null, "files": ["build.mjs", "src/index.html", "src/about.html", "package.json"], "file_count": 4}, "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": false}
{"id": "f-01", "request": "Add a multiply function to calc with tests, matching how add and subtract are done.", "chat": [], "repo": {"test_cmd": "pytest", "files": ["calc.py", "tests/test_calc.py", "pyproject.toml"], "file_count": 3}, "expected_class": "feature", "expected_depth": "full", "ambiguous": false}
{"id": "v-01", "request": "make it better", "chat": [], "repo": {"test_cmd": "pytest", "files": ["calc.py", "tests/test_calc.py", "pyproject.toml"], "file_count": 3}, "expected_class": "other", "expected_depth": "full", "ambiguous": true}
```

`f-01` is deliberately `feature`: adding a function with tests needs a TDD task and design choices. It matches the end-to-end `py-multiply` case, which M3b expects to take the quick path. Leave it as written. The disagreement between the two is something the benchmark should show.

**Metrics** (`metrics.py`, pure, tested offline). Each record is `{case_id, backend, expected_class, expected_depth, ambiguous, task_class, probabilities, confidence, needs_detail, latency_ms, input_tokens, output_tokens, cost_usd, error}`. `summarise` returns:

```python
{
  "n": int, "errors": int,
  "class_accuracy": float,          # over non-error records
  "depth_accuracy": float,          # decide(...) depth (None = "intake") vs expected_depth; intake counts as wrong
  "intake_rate": float,             # share routed to intake
  "confusion": {expected_depth: {routed_depth_or_"intake": count}},
  "answer_as_change": int,          # expected answer, routed quick/full: the worst error (an unwanted edit)
  "change_as_answer": int,          # expected quick/full, routed answer: a wasted turn, nothing changed
  "quick_as_full": int, "full_as_quick": int,
  "detail_precision": float, "detail_recall": float,   # needs_detail >= detail_threshold vs ambiguous
  "latency_p50_ms": int, "latency_p95_ms": int,
  "cost_per_100": float | None,     # None if any cost is unknown
}
```

`sweep` replays `summarise` for each confidence threshold, keeping `detail_threshold` fixed, and returns `[{threshold, depth_accuracy, intake_rate}, ...]`.

Spec §5.1 names "answer mistaken for change" as the worst error: a question routed to a change path, where Phil could edit code nobody asked it to change. `answer_as_change` counts exactly that, and the decision rule uses it.

`test_metrics_offline.py` builds about 8 hand-written records and asserts every field of `summarise`, plus one `sweep` row, with exact numbers. Write it before `metrics.py`, run it and see it fail, then implement.

**Runner** (`run.py`):
- `run_backend(backend: Literal["jev", "llm"], cases, config) -> list[dict]` runs each case through:
  - `judge_jev`, with the key from `key_lookup()`;
  - or `judge_llm`, with an `AgentContext` on a temporary database, using `config`'s `low` model through `model=config.tier_model("low")`.

  An exception becomes a record with `error` set to the exception's class name and `JevError.reason`, never its message.
- Records append to `~/.phil/bench/classify.jsonl`, or `$PHIL_BENCH_CLASSIFY_RESULTS`, each with `phil_sha` (reuse `harness.phil_sha`), `backend`, the model and a timestamp.
- `--report` prints, per backend, the latest run's `summarise` at the configured thresholds, then the sweep table, then the spec §5.1 decision rule evaluated (`jev worth recommending: yes/no`, with the three conditions shown).

`test_classify_bench.py`:

```python
import os
from pathlib import Path

import pytest

from tests.live.bench.classify.run import load_cases, run_and_record


@pytest.mark.bench
@pytest.mark.parametrize("backend", ["llm", "jev"])
def test_classify_bench(backend):
    config = os.environ.get("PHIL_BENCH_CONFIG")
    if not config:
        pytest.skip("PHIL_BENCH_CONFIG is unset: point it at a phil.toml with the [models] to benchmark")
    summary = run_and_record(backend, load_cases(), Path(config).expanduser())
    if summary is None:
        pytest.skip(f"{backend}: not configured (no TYPESAFE_API_KEY for jev)")
    assert summary["n"] == 40 and summary["errors"] < 40
```

`run_and_record` returns `None` for `jev` when `key_lookup().get("TYPESAFE_API_KEY")` is empty.

**Docs:**
- `README.md`: a **Routing** section covering:
  - the three paths;
  - the status line;
  - `/ask`, `/quick` and `/full`;
  - `[models] classifier = "typesafe:jev-latest"` with `phil keys set typesafe`;
  - the fallback;
  - `[routing]` thresholds;
  - the classifier benchmark command: `PHIL_BENCH_CONFIG=~/Code/phil-bench.toml uv run pytest -m bench tests/live/bench/classify -n 0`, then `uv run python -m tests.live.bench.classify.run --report`.
- `roadmap.md`: M3 is split into M3a (this) and M3b. M3a moves to Done after merge (leave it in progress; the controller moves it).
- `2026-09-30-phil-m3a-followups.md`:
  - Jev calls aren't in run or chat telemetry, because there are no prices yet;
  - the classify primitive could be reused for tdd versus check mode, chat reply intent, and whether a review finding blocks;
  - a Jev-specific cost estimate, once TypeSafe publishes prices;
  - the thresholds are to be set from the first benchmark run.

- [ ] **Step 1:** Write `test_metrics_offline.py`, run it and see it fail, then implement `metrics.py` and run it again to see it pass.
- [ ] **Step 2:** Write `cases.jsonl` (40 lines). Add an offline test that loads it and asserts the counts per kind in the table above, that every `expected_class` is in `CLASSES`, and that every `expected_depth` is in `DEPTHS`.
- [ ] **Step 3:** Write `run.py` and `test_classify_bench.py`. Run `uv run pytest tests/live/bench/classify -q -n 0`: the offline tests pass, and the bench test is deselected by the default `-m 'not live and not bench'`.
- [ ] **Step 4:** Docs.
- [ ] **Step 5:** Run the full suite: `uv run pytest -q`.
- [ ] **Step 6: Commit.** Message: `Add the classifier benchmark and routing docs`, plus the trailer.
