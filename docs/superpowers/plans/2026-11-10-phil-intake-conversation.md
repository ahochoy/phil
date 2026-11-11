# Intake as a Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phil talks with the user before it works: one structured `converse` call per user message returns a reply, optional choices, and, when ready, the goal plus the steps the user always sees; today's intake stays behind `[chat] intake = "classic"`.

**Architecture:** New contracts (`ConverseTurn`, `ConverseInput`, `IntakeTurn`) and a read-only `converse` agent spec. The chat controller gains two stages, `conversation` (waiting for the user) and `conversing` (a turn in flight), driven by `turn_ready` / `turn_failed` events like every other chat job. A ready turn hands its `Goal` to the existing quick path or full path (designer, architect, critic, approval); the architect and designer also receive the conversation.

**Tech Stack:** Python 3.14, Pydantic v2 contracts, LangChain agents through `phil.agents.invoke.invoke_agent`, pytest with `ScriptedAgentFactory` / `ChatFactory` fakes.

**Spec:** `docs/superpowers/specs/2026-11-10-phil-intake-conversation-design.md`

## Global Constraints

- Edit repo files only with Edit/Write. Never sed, python, heredocs or printf on repo files.
- Commit with `git commit -F .git/phil-commit-msg.txt` (write the message with Write). Every message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage only your own files.
- Test command: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q` (append paths / `-k` to narrow). Never run `-m live` or `-m bench`.
- `CONVERSE_MAX_MODEL_CALLS = 6`; `MAX_CONVERSATION_TURNS = 4`; choices are 0 or 2–4; steps are 2–7 when ready.
- `[chat] intake = "conversation" | "classic"`, default `"conversation"`.
- Forced paths (`/ask`, `/quick`, `/full`, `/full!`), accepted fix offers and the `answer` route never open a conversation.
- User-facing copy: communicative, not chatty; no filler (memory: chat-ux-principles).

## Review Focus

1. A typed number that matches no choice (e.g. `7` with 3 choices, or `0`) — it is the user's own answer, passed as written, never an error or a re-prompt (test in Task 4).
2. Ctrl-C while a turn is in flight — the goal is cancelled, the late `turn_ready` is dropped as stale, the chat is idle (test in Task 5).
3. A ready quick goal whose task is missing or fails the launch checks — the Plan block still shows, then the existing quick fallback plans it fully (test in Task 4).
4. A chat closed while a turn was in flight, then reopened — it resumes the conversation and re-sends the unanswered turn (test in Task 6).
5. A message typed after Phil's ready turn was computed but while it was still in flight — not lost: the ready turn is shown and the conversation continues with the new message instead of launching (test in Task 5).

---

### Task 1: Conversation contracts

**Files:**
- Create: `src/phil/contracts/converse.py`
- Modify: `src/phil/contracts/inputs.py` (ArchitectInput), `src/phil/contracts/design.py` (DesignInput), `src/phil/contracts/__init__.py`
- Test: `tests/test_converse_contracts.py`

**Interfaces:**
- Produces: `ConverseTurn(speaker: Literal["user","phil"], text: str, choices: list[str] = [])` (a `Part`); `ConverseInput(turns: list[ConverseTurn], route_depth: Literal["answer","quick","full"] | None = None, route_class: TaskClass | None = None, repo_overview: str = "", detected_test_cmd: str | None = None, must_decide: bool = False)`; `IntakeTurn(reply: str, choices: list[str] = [], ready: bool, goal: Goal | None = None, steps: list[str] = [])`; `ArchitectInput.conversation: list[ConverseTurn] | None = None`; `DesignInput.conversation: list[ConverseTurn] | None = None`; constants `MIN_STEPS = 2`, `MAX_STEPS = 7`. All exported from `phil.contracts`; `ConverseInput` and `IntakeTurn` added to `ALL_CONTRACTS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_converse_contracts.py
import pytest
from pydantic import ValidationError

from phil.contracts import (
    ALL_CONTRACTS,
    ArchitectInput,
    ConverseInput,
    ConverseTurn,
    DesignInput,
    Goal,
    IntakeTurn,
    QuickTask,
)

QUICK = Goal(
    objective="Fix the README heading typo",
    depth="quick",
    task=QuickTask(id="DOCS-001", description="Fix the typo", acceptance_criteria=["heading reads Install"]),
)
FULL = Goal(objective="Add a pricing page", depth="full")
STEPS = ["Find the heading", "Fix the spelling"]


def test_a_turn_that_is_not_ready_asks_with_choices():
    turn = IntakeTurn(reply="Which page?", choices=["Home", "Pricing"], ready=False)
    assert (turn.goal, turn.steps) == (None, [])


def test_a_ready_turn_carries_a_goal_and_steps():
    turn = IntakeTurn(reply="Fixing the typo.", ready=True, goal=QUICK, steps=STEPS)
    assert turn.goal.depth == "quick"


@pytest.mark.parametrize(
    "fields",
    [
        {"ready": True, "steps": STEPS},  # no goal
        {"ready": True, "goal": QUICK},  # no steps
        {"ready": True, "goal": QUICK, "steps": ["One"]},  # too few steps
        {"ready": True, "goal": QUICK, "steps": [f"Step {n}" for n in range(8)]},  # too many
        {"ready": False, "goal": QUICK},  # a goal on a turn that isn't ready
        {"ready": False, "steps": STEPS},  # steps on a turn that isn't ready
        {"ready": True, "goal": Goal(objective="Add a pricing page", depth="answer"), "steps": STEPS},
        {"ready": True, "goal": Goal(objective="Add a pricing page"), "steps": STEPS},  # no depth
        {"ready": True, "goal": FULL.model_copy(update={"open_questions": [{"text": "Which plan?"}]}), "steps": STEPS},
        {"ready": False, "choices": ["Only one"]},
        {"ready": False, "choices": ["a", "b", "c", "d", "e"]},
        {"ready": False, "reply": "   "},
    ],
)
def test_invalid_turns_are_rejected(fields):
    with pytest.raises(ValidationError):
        IntakeTurn(**({"reply": "Okay."} | fields))


def test_blank_choices_are_dropped():
    assert IntakeTurn(reply="Which?", choices=[" a ", "", "b"], ready=False).choices == ["a", "b"]


def test_converse_input_defaults():
    data = ConverseInput(turns=[ConverseTurn(speaker="user", text="fix it")]).model_dump()
    assert data["must_decide"] is False
    assert (data["route_depth"], data["route_class"], data["detected_test_cmd"]) == (None, None, None)


def test_architect_and_designer_take_the_conversation():
    turns = [ConverseTurn(speaker="user", text="add pricing"), ConverseTurn(speaker="phil", text="Which plans?")]
    assert ArchitectInput(goal=FULL, conversation=turns).conversation == turns
    assert DesignInput(goal=FULL, conversation=turns).conversation == turns
    assert ArchitectInput(goal=FULL).conversation is None


def test_new_contracts_are_listed():
    assert ConverseInput in ALL_CONTRACTS and IntakeTurn in ALL_CONTRACTS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/test_converse_contracts.py`
Expected: FAIL with `ImportError: cannot import name 'ConverseInput'`.

- [ ] **Step 3: Write the contracts**

```python
# src/phil/contracts/converse.py
"""The intake conversation (spec 2026-11-10 §4.2): its turns, the converse agent's input, and each
turn's output."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.interface import MAX_OPTIONS, Goal
from phil.contracts.routing import TaskClass

MIN_STEPS, MAX_STEPS = 2, 7


class ConverseTurn(Part):
    speaker: Literal["user", "phil"] = Field(description="Who said it.")
    text: str = Field(description="What they said.")
    choices: list[str] = Field(default=[], description="The choices Phil offered with this message, if any.")


class ConverseInput(Contract):
    turns: list[ConverseTurn]
    route_depth: Literal["answer", "quick", "full"] | None = None
    route_class: TaskClass | None = None
    repo_overview: str = ""
    detected_test_cmd: str | None = None
    must_decide: bool = False


class IntakeTurn(Contract):
    reply: str = Field(description="Your message to the user: a few sentences at most, plain prose, no filler.")
    choices: list[str] = Field(
        default=[],
        description="0, or 2 to 4 short answers the user can pick from when your question has a few clear "
        "alternatives, most likely first. Don't add an \"other\" option: the chat adds one.",
    )
    ready: bool = Field(description="True when you know enough to start: then `goal` and `steps` are required.")
    goal: Goal | None = Field(
        default=None,
        description="Only when ready: the goal, with depth `quick` (and its `task`) or `full`, and no open questions.",
    )
    steps: list[str] = Field(
        default=[],
        description="Only when ready: 2 to 7 one-line steps, in the user's terms, saying what Phil will do.",
    )

    @field_validator("reply")
    @classmethod
    def _says_something(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reply must say something")
        return value.strip()

    @field_validator("choices")
    @classmethod
    def _usable_choices(cls, value: list[str]) -> list[str]:
        choices = [choice.strip() for choice in value if choice.strip()]
        if len(choices) == 1 or len(choices) > MAX_OPTIONS:
            raise ValueError(f"choices must be empty or 2 to {MAX_OPTIONS} options")
        return choices

    @model_validator(mode="after")
    def _ready_means_a_goal(self) -> "IntakeTurn":
        if not self.ready:
            if self.goal is not None or self.steps:
                raise ValueError("a turn that isn't ready has no goal and no steps")
            return self
        if self.goal is None:
            raise ValueError("a ready turn needs its goal")
        if self.goal.depth not in ("quick", "full"):
            raise ValueError("a ready goal's depth is quick or full")
        if self.goal.open_questions:
            raise ValueError("a ready goal has no open questions: ask them in the reply instead")
        if not MIN_STEPS <= len(self.steps) <= MAX_STEPS:
            raise ValueError(f"a ready turn has {MIN_STEPS} to {MAX_STEPS} steps")
        return self
```

In `src/phil/contracts/inputs.py`, add `from phil.contracts.converse import ConverseTurn` to the imports and this field at the end of `ArchitectInput`:

```python
    conversation: list[ConverseTurn] | None = Field(
        default=None,
        description="When set, the user's conversation with Phil before planning. The goal summarises it; "
        "where they differ, the user's own words win.",
    )
```

In `src/phil/contracts/design.py`, add `from phil.contracts.converse import ConverseTurn` and the same field (same description) at the end of `DesignInput`.

In `src/phil/contracts/__init__.py`: import `from phil.contracts.converse import ConverseInput, ConverseTurn, IntakeTurn`, append `ConverseInput` and `IntakeTurn` to `ALL_CONTRACTS`, and add `"ConverseInput"`, `"ConverseTurn"`, `"IntakeTurn"` to `__all__` in alphabetical position.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/test_converse_contracts.py tests/test_contracts.py tests/test_cli.py tests/test_contract_descriptions.py tests/test_ask_contracts.py`
Expected: PASS (the schema-export tests count `ALL_CONTRACTS`, so they pick up the two new contracts).

- [ ] **Step 5: Commit**

Message: `Contracts: the intake conversation's turns, input and output`, body: one line on the validator rules (ready needs a quick/full goal without open questions and 2–7 steps; choices 0 or 2–4). Stage the five files.

---

### Task 2: The `converse` spec, its prompt, its call, and the setting

**Files:**
- Create: `src/phil/prompts/converse.md`
- Modify: `src/phil/agents/spec.py` (add `REPLY_NOW`), `src/phil/agents/registry.py`, `src/phil/chat/planning.py` (add `converse`), `src/phil/config.py` (`ChatConfig.intake`)
- Test: `tests/chat/test_converse_spec.py`, `tests/agents/test_registry.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Task 1's `ConverseInput`, `ConverseTurn`, `IntakeTurn`.
- Produces: `get_spec("converse")`; `CONVERSE_MAX_MODEL_CALLS = 6` in `phil.agents.registry`; `REPLY_NOW` in `phil.agents.spec`; `phil.chat.planning.converse(ctx: AgentContext, turns: Sequence[ConverseTurn], *, tree: Path, overview: str, route_depth: str | None = None, route_class: str | None = None, detected_test_cmd: str | None = None, must_decide: bool = False, call: int = 1) -> IntakeTurn`; `PhilConfig.chat.intake: Literal["conversation", "classic"]`, default `"conversation"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/chat/test_converse_spec.py
from phil.agents.fake import ScriptedAgentFactory
from phil.agents.registry import CONVERSE_MAX_MODEL_CALLS, get_spec
from phil.agents.spec import REPLY_NOW, load_prompt
from phil.chat.planning import converse
from phil.contracts import ConverseInput, ConverseTurn, IntakeTurn


def test_converse_spec():
    spec = get_spec("converse")
    assert (spec.role, spec.in_contract, spec.out_contract, spec.harness, spec.tools) == (
        "orchestrator", ConverseInput, IntakeTurn, "light", ("shell",)
    )
    assert spec.read_only_shell and spec.repo_context and not spec.writes_files and not spec.shared_prompt
    assert (spec.max_model_calls, spec.cap_message) == (CONVERSE_MAX_MODEL_CALLS, REPLY_NOW) == (6, REPLY_NOW)
    assert "# Role: Intake conversation" in load_prompt(spec)


def test_converse_sends_the_conversation_and_reads_the_snapshot(chat_ctx, tmp_path):
    out = IntakeTurn(reply="Which page?", choices=["Home", "Pricing"], ready=False)
    factory = ScriptedAgentFactory({"converse": [out]})
    turns = [ConverseTurn(speaker="user", text="add a banner")]
    got = converse(
        chat_ctx(factory), turns, tree=tmp_path, overview="calc.py", route_depth="quick",
        route_class="simple_change", detected_test_cmd="pytest", must_decide=True, call=3,
    )
    assert got == out
    role, payload = factory.calls[0]
    assert role == "converse"
    text = str(payload)
    for expected in ("add a banner", "simple_change", "must_decide", "calc.py"):
        assert expected in text
```

Append to `tests/agents/test_registry.py`: add `"converse"` to the set in `test_registry_covers_chat_and_run_roles` (the roles assertion is unchanged: `converse` uses `orchestrator`).

Append to `tests/test_config.py`:

```python
def test_chat_intake_defaults_to_the_conversation():
    assert PhilConfig(models=TEST_MODELS).chat.intake == "conversation"


def test_chat_intake_can_be_classic():
    assert PhilConfig(models=TEST_MODELS, chat={"intake": "classic"}).chat.intake == "classic"


def test_chat_intake_rejects_other_values():
    with pytest.raises(ValidationError):
        PhilConfig(models=TEST_MODELS, chat={"intake": "chatty"})
```

(Use the imports `tests/test_config.py` already has; add `from pydantic import ValidationError` and `from tests.helpers import TEST_MODELS` if missing. If `PhilConfig` wraps validation errors in `ConfigError` elsewhere, keep `ValidationError` here: this builds the model directly.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat/test_converse_spec.py tests/agents/test_registry.py tests/test_config.py`
Expected: FAIL with `ImportError: cannot import name 'CONVERSE_MAX_MODEL_CALLS'` (and the config tests with `AttributeError ... 'intake'`).

- [ ] **Step 3: Implement**

`src/phil/agents/spec.py`, after `PROPOSE_NOW`:

```python
REPLY_NOW = "Your tool budget is used up. Reply now with what you have read; don't read more."
```

`src/phil/agents/registry.py`: import `REPLY_NOW` with the other cap messages, import `ConverseInput, IntakeTurn` from `phil.contracts`, add after `DESIGN_MAX_MODEL_CALLS`:

```python
# The intake conversation's model-call budget per turn: a turn reads a few files at most.
CONVERSE_MAX_MODEL_CALLS = 6
```

and in `SPECS`, after `"intake"`:

```python
    # One turn of the intake conversation (spec 2026-11-10 §4.2): read-only, like the answerer.
    "converse": AgentSpec(
        "converse", "orchestrator", ConverseInput, IntakeTurn, tools=("shell",), harness="light",
        shared_prompt=False, read_only_shell=True, max_model_calls=CONVERSE_MAX_MODEL_CALLS,
        cap_message=REPLY_NOW, repo_context=True,
    ),
```

`src/phil/chat/planning.py`: add `ConverseInput, ConverseTurn, IntakeTurn` to the `phil.contracts` import and add after `intake`:

```python
def converse(
    ctx: AgentContext,
    turns: Sequence[ConverseTurn],
    *,
    tree: Path,
    overview: str,
    route_depth: Literal["answer", "quick", "full"] | None = None,
    route_class: str | None = None,
    detected_test_cmd: str | None = None,
    must_decide: bool = False,
    call: int = 1,
) -> IntakeTurn:
    """One turn of the intake conversation (spec 2026-11-10 §4.2): the converse agent reads `tree`,
    the base commit's snapshot (as the architect does), read-only."""
    contract = ConverseInput(
        turns=list(turns),
        route_depth=route_depth,
        route_class=route_class,
        repo_overview=overview,
        detected_test_cmd=detected_test_cmd,
        must_decide=must_decide,
    )
    packet = build_packet("orchestrator", contract, budget_tokens=_budget(ctx, "orchestrator"))
    out = invoke_agent(get_spec("converse"), packet, replace(ctx, workdir=tree), node="converse", call=call)
    assert isinstance(out, IntakeTurn)
    return out
```

`src/phil/config.py`, in `ChatConfig` after `max_cost_usd` (add `Literal` to the `typing` import if missing):

```python
    # How a goal's intake works: "conversation" talks with the user turn by turn (spec 2026-11-10);
    # "classic" is the one-call intake with question menus.
    intake: Literal["conversation", "classic"] = "conversation"
```

`src/phil/prompts/converse.md`:

```markdown
# Role: Intake conversation

You talk with the user about what they want done in this repository, until you know enough to start. You never change anything and you never write the plan; a planner and implementers do the work after you.

## Tools
- `ls`, `read_file`, `glob`, `grep` on the repository, and a shell that runs only read-only commands (`git log`, `grep`, `cat`, ...).
- File-tool paths start at the repository root with a leading `/`, for example `/src/app/page.tsx`; there is no `/repo` or other prefix.
- Shell commands run in the repository root: give them relative paths, never the file tools' leading `/`.
- Read only what makes your reply specific: find the file or section the user means before you ask about it. Start from `repo_overview`.

## Each turn
- `turns` is the conversation so far, oldest first; the last one is the user's.
- `reply`: a short, direct message, a few sentences at most. No filler, no praise, no restating what the user said.
- Ask at most one or two things a turn. When the answer is one of a few alternatives, put them in `choices` (2 to 4, most likely first, drawn from the repo and the request). Don't add an "other" choice; the chat adds one.
- `route_depth` and `route_class` are a quick classifier's guess. Treat them as a hint: you decide the size.

## When to ask
- Ask when the change depends on something only the user can supply: link targets or URLs; copy, product or offer names; images or assets; which page or section; placement; visual style for visual changes.
- Never ask what the repository answers: read it instead.
- Don't invent that content and don't plan placeholders unless the user chose placeholders.
- When the request is clear, be ready on the first turn.

## When ready
- Set `ready` to true, say in `reply` what you understood in a line or two, and fill `goal` and `steps`.
- `goal`: `objective` in the user's terms, `name`, `constraints` (everything the user told you), `non_goals`, `approach_open` (several reasonable ways to build it and the user hasn't chosen), and `depth`:
  - `quick`: one small, well-specified change. Write its `task`: `id` as KEYWORD-001 (3 to 6 uppercase letters), a one-sentence `description`, observable `acceptance_criteria`, `files_hint`, and `verify` (`check` with a `check_cmd` for copy, markup, config or docs; `tdd` when behaviour changes and `detected_test_cmd` is set).
  - `full`: anything needing design, several files, or a plan. No `task`.
  - Leave `open_questions` empty: anything still open belongs in your reply, with `ready` false.
- `steps`: 2 to 7 one-line steps saying what will happen, in the user's terms (e.g. "Fix the heading's spelling in README.md", "Check the README renders").
- If `must_decide` is true you must be ready this turn: decide with what you know, and say in `reply` what you assumed.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat/test_converse_spec.py tests/agents/test_registry.py tests/test_config.py tests/agents`
Expected: PASS. (If a test lists every prompt file or every spec's prompt, add `converse` there.)

- [ ] **Step 5: Commit**

Message: `The converse agent: read-only, one turn of the intake conversation`. Stage the six source/prompt files and the three test files.

---

### Task 3: Existing chat tests pin the classic intake

The controller doesn't read `chat.intake` yet, so this task changes no behaviour; it makes the next task's default switch safe. Every existing test that scripts `"intake"` for a chat exercises the classic flow and must keep doing so.

**Files:**
- Modify: `tests/helpers.py`, `tests/chat/test_controller.py` (`run_chat` and its direct `ChatController(...)` calls), and every other test module whose chat config scripts `"intake"` (find them as in Step 1)
- Test: the existing suite

**Interfaces:**
- Produces: `tests.helpers.CLASSIC_CHAT = {"intake": "classic"}` and `tests.helpers.classic_config(**kw) -> PhilConfig` (= `PhilConfig(models=TEST_MODELS, chat=CLASSIC_CHAT | kw.pop("chat", {}), **kw)`); `run_chat(..., config=None)` defaults to `classic_config()`.

- [ ] **Step 1: Find the sites**

Run: `grep -rn "PhilConfig(" tests/chat tests/cli/test_setup_autostart.py` and `grep -rln '"intake"' tests/chat`.
Every `PhilConfig(...)` that reaches a `ChatController` (through `run_chat`, the `controller` fixtures in `test_controller_status.py` / `test_controller_callouts.py`, `test_terminal.py`, or a direct `ChatController(...)`) gets `chat=CLASSIC_CHAT` merged in. Configs loaded from a repo's `phil.toml` by the test (e.g. `load_config`) are not changed: if one of those tests scripts `"intake"`, write `[chat]\nintake = "classic"\n` into that test's `phil.toml` with the test's own file write.

- [ ] **Step 2: Add the helper**

```python
# tests/helpers.py, after TEST_MODELS / MODELS_TOML
# Chat tests that script "intake" exercise the classic intake (spec 2026-11-10 §4.8); the
# conversation has its own tests.
CLASSIC_CHAT = {"intake": "classic"}


def classic_config(**kw) -> "PhilConfig":
    from phil.config import PhilConfig

    chat = CLASSIC_CHAT | kw.pop("chat", {})
    return PhilConfig(models=kw.pop("models", TEST_MODELS), chat=chat, **kw)
```

In `run_chat`: `config = config or classic_config()`. Replace each other site's `PhilConfig(models=TEST_MODELS, ...)` with `classic_config(...)` (keeping its other arguments), and for configs built with `chat={...}` pass that dict to `classic_config(chat={...})`.

- [ ] **Step 3: Run the suite**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q`
Expected: PASS, same count as before this task (the controller still ignores the setting).

- [ ] **Step 4: Prove the pinning is complete**

Add a guard that fails any chat test whose config doesn't say which intake it exercises, to `tests/chat/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _chat_tests_choose_their_intake(monkeypatch):
    """Guard (spec 2026-11-10 §4.8): a chat test that doesn't say which intake it exercises fails,
    so the default can never silently change what an old test covers."""
    from phil.chat import controller as module

    real = module.ChatController.__init__

    def init(self, *args, **kw):
        config = kw["config"] if "config" in kw else args[1]  # ChatController(info, config, ...)
        assert "intake" in config.chat.model_fields_set, "chat tests must set chat.intake (classic_config())"
        real(self, *args, **kw)

    monkeypatch.setattr(module.ChatController, "__init__", init)
```

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat tests/cli`
Expected: PASS. Fix any site the guard names, then rerun. Keep the guard: Task 4's conversation tests set `chat={"intake": "conversation"}` explicitly. (A config reloaded mid-chat by the controller through `load_config` isn't checked by the guard; only the constructor's.)

- [ ] **Step 5: Commit**

Message: `Tests: chat tests say which intake they exercise`. Stage the test files only.

---

### Task 4: The conversation flow in the chat

**Files:**
- Modify: `src/phil/chat/controller.py`, `src/phil/chat/decision.py`, `src/phil/ui/toolbar.py`, `src/phil/ui/plan_view.py`
- Test: `tests/chat/test_conversation_flow.py`

**Interfaces:**
- Consumes: `converse` (Task 2), `ConverseTurn`, `IntakeTurn` (Task 1), `classic_config` / `CLASSIC_CHAT` (Task 3), `run_chat` from `tests/chat/test_controller.py`.
- Produces (controller): stages `"conversation"` and `"conversing"`; events `turn_ready` / `turn_failed`; methods `_open_conversation(text)`, `_turn_job(*, must_decide=False, check_prices=False)`, `_on_turn_ready(data)`, `_conversation_input(text)`, `_user_turn(text)`, `_finish_conversation(turn)`; attributes `_intake_mode: str`, `_turns: list[ConverseTurn]`, `_choices: list[str]`, `_user_replies: int`, `_held_input: list[str]`, `_typing_choice_other: bool`, `_converse_calls: int`. `decision.conversation_decision(choices: list[str]) -> Decision`. `plan_view.render_steps(console, steps: list[str]) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/chat/test_conversation_flow.py
from phil.contracts import Goal, IntakeTurn, QuickTask
from phil.contracts.routing import RouteJudgement
from tests.chat.conftest import critique, plan
from tests.chat.test_controller import run_chat
from tests.helpers import TEST_MODELS

from phil.config import PhilConfig

CONVERSATION = PhilConfig(models=TEST_MODELS, chat={"intake": "conversation"})
STEPS = ["Add subtract to calc.py", "Test it"]
QUICK_TASK = QuickTask(
    id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"],
    files_hint=["calc.py"], verify="tdd",
)


def quick_goal(**kw):
    return Goal(objective="Add subtract to calc", depth="quick", task=QUICK_TASK, **kw)


def full_goal(**kw):
    return Goal(objective="Add a calculator CLI", depth="full", **kw)


def ready(goal, reply="Adding subtract.", steps=STEPS):
    return IntakeTurn(reply=reply, ready=True, goal=goal, steps=steps)


def asks(reply="Which module?", choices=()):
    return IntakeTurn(reply=reply, choices=list(choices), ready=False)


def payloads(factory, role):
    return [str(payload) for name, payload in factory.calls if name == role]


def test_a_clear_request_shows_its_plan_and_starts(calc_repo):
    text, spawned, runs, factory, _ = run_chat(
        calc_repo, ["add subtract"], {"converse": [ready(quick_goal())]}, config=CONVERSATION
    )
    assert "Adding subtract." in text
    assert "Plan" in text and "Add subtract to calc.py" in text and "Test it" in text
    assert "Starting (quick)" in text
    assert [mode for _, mode, _ in spawned] == ["start"]
    assert factory.remaining() == {"converse": 0}


def test_a_question_then_an_answer_then_ready(calc_repo):
    text, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "calc.py please"],
        {"converse": [asks(), ready(quick_goal())]}, config=CONVERSATION,
    )
    assert "Which module?" in text
    second = payloads(factory, "converse")[1]
    assert "add subtract" in second and "Which module?" in second and "calc.py please" in second
    assert len(spawned) == 1


# Choice labels that appear nowhere else in a payload (not in the repo overview), so counting
# them says whether the user's turn is the choice: once in Phil's turn's choices, once more if picked.
CHOICES = ["Option Alpha", "Option Beta"]


def test_a_typed_number_picks_a_choice(calc_repo):
    _, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "2"],
        {"converse": [asks(choices=CHOICES), ready(quick_goal())]}, config=CONVERSATION,
    )
    second = payloads(factory, "converse")[1]
    assert second.count("Option Beta") == 2 and second.count("Option Alpha") == 1


def test_a_number_that_matches_no_choice_is_the_users_own_answer(calc_repo):
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "7"],
        {"converse": [asks(choices=CHOICES), ready(quick_goal())]}, config=CONVERSATION,
    )
    calls = payloads(factory, "converse")
    assert len(calls) == 2  # sent as an answer, not re-prompted
    assert calls[1].count("Option Beta") == 1 and calls[1].count("Option Alpha") == 1
    assert '"text": "7"' in calls[1]
    assert len(spawned) == 1


def test_a_ready_full_goal_goes_to_the_architect_with_the_conversation(calc_repo):
    text, spawned, _, factory, _ = run_chat(
        calc_repo, ["build a calculator CLI", "argparse", "y"],
        {"converse": [asks("Which CLI library?"), ready(full_goal(), reply="Planning the CLI.")],
         "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "Planning in detail" in text
    architect = payloads(factory, "architect")[0]
    assert "Which CLI library?" in architect and "argparse" in architect
    assert len(spawned) == 1  # approved with "y"


def test_an_open_approach_goes_to_the_designer_with_the_conversation(calc_repo):
    from phil.contracts import Approach, Approaches

    approaches = Approaches(options=[Approach(name="Argparse", summary="a"), Approach(name="Click", summary="b")])
    _, _, _, factory, _ = run_chat(  # EOF at the approach menu ends the chat
        calc_repo, ["build a calculator CLI"],
        {"converse": [ready(full_goal(approach_open=True))], "design": [approaches]},
        config=CONVERSATION,
    )
    assert "build a calculator CLI" in payloads(factory, "design")[0]


def test_the_conversation_may_make_a_quick_route_full(calc_repo):
    quick_route = RouteJudgement(task_class="simple_change", confidence=0.95, needs_detail=0.1)
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"],
        {"route": [quick_route], "converse": [ready(full_goal())], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "simple_change" in payloads(factory, "converse")[0]
    assert payloads(factory, "architect") and spawned == []  # full path, plan declined with "n"


def test_the_conversation_may_make_a_full_route_quick(calc_repo):
    full_route = RouteJudgement(task_class="feature", confidence=0.95, needs_detail=0.1)
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract"], {"route": [full_route], "converse": [ready(quick_goal())]}, config=CONVERSATION
    )
    assert len(spawned) == 1 and not payloads(factory, "architect")


def test_a_quick_goal_without_a_valid_task_still_shows_its_plan_then_plans_fully(calc_repo):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"],
        {"converse": [ready(Goal(objective="Add subtract to calc", depth="quick"))],
         "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "Add subtract to calc.py" in text  # the Plan block
    assert "Couldn't plan this as a quick change" in text
    assert payloads(factory, "architect")


def test_forced_paths_skip_the_conversation(calc_repo):
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["/quick add subtract"], {"intake": [quick_goal()]}, config=CONVERSATION
    )
    assert not payloads(factory, "converse") and len(spawned) == 1


def test_questions_still_go_to_the_answerer(calc_repo):
    from phil.contracts.routing import Answer

    question = RouteJudgement(task_class="question", confidence=0.95, needs_detail=0.1)
    text, _, _, factory, _ = run_chat(
        calc_repo, ["what does calc do?"],
        {"route": [question], "answer": [Answer(text="It adds.")]}, config=CONVERSATION,
    )
    assert "It adds." in text and not payloads(factory, "converse")


def test_the_menu_offers_the_choices(calc_repo):
    calls = []
    run_chat(
        calc_repo, ["add subtract", "2"],
        {"converse": [asks(choices=["app.py", "calc.py"]), ready(quick_goal())]},
        config=CONVERSATION, choose=True, calls=calls,
    )
    menus = [call[2] for call in calls if call[0] == "choose"]
    labels = [option.label for option in menus[0].options]
    assert labels == ["app.py", "calc.py", "Something else (type it)", "Plan with what you know"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat/test_conversation_flow.py`
Expected: FAIL — the controller still runs the classic intake (`no scripted output left for intake`).

- [ ] **Step 3: Implement**

`src/phil/ui/toolbar.py` `STEP_LABELS`: add `"conversing": "Thinking",`.

`src/phil/ui/plan_view.py`:

```python
def render_steps(console: Console, steps: list[str]) -> None:
    """The Plan block a ready intake turn shows before anything runs (spec 2026-11-10 §4.4)."""
    console.print("[phil.brand]Plan:[/]")
    for n, step in enumerate(steps, 1):
        console.print(f"  {n}. {escape(_clip(step))}")
```

`src/phil/chat/decision.py`, after `question_decision`:

```python
def conversation_decision(choices: list[str]) -> Decision:
    """The intake conversation's choices (spec 2026-11-10 §4.4): each by its number, then typing
    your own answer, then "Plan with what you know"."""
    opts = [Option(choice, str(n)) for n, choice in enumerate(choices, 1)]
    opts.append(Option("Something else (type it)", str(len(choices) + 1), typed=True))
    opts.append(Option("Plan with what you know", "go"))
    return Decision("question", "Pick one, or type your answer", (), tuple(opts))
```

`src/phil/chat/controller.py`:

1. Imports: add `conversation_decision` to the `phil.chat.decision` import, `converse` to the `phil.chat.planning` import, `ConverseTurn, IntakeTurn` to the `phil.contracts` import, `render_steps` next to the existing `render_goal` import.
2. Constants, after `DESIGN_FALLBACK`:

```python
MAX_CONVERSATION_TURNS = 4  # user replies before the next turn must decide (spec 2026-11-10 §4.6)
CONVERSATION_STAGES = ("conversation", "conversing")  # waiting for the user; a turn in flight
STARTING_QUICK = "Starting (quick)…"
PLANNING_IN_DETAIL = "Planning in detail…"
```

   `TRANSCRIPT_STAGES`: add `"conversation": "answers", "conversing": "answers"`. `JOB_ROLES`: add `"turn_ready": "orchestrator"`. `STEP_ROLES`: add `"conversing": "orchestrator"`.
3. `__init__`, after `self._design_calls = 0`:

```python
        self._converse_calls = 0
        self._intake_mode = "classic"  # the goal's intake, read from [chat] intake when it begins
        self._turns: list[ConverseTurn] = []  # the intake conversation so far
        self._choices: list[str] = []  # the choices Phil's last turn offered
        self._user_replies = 0  # the user's messages after the opening one
        self._held_input: list[str] = []  # typed while a turn was in flight: joins the next turn
        self._typing_choice_other = False  # "Something else" was picked: the next input is the answer
```

4. `_reset_goal`: add `self._turns, self._choices, self._user_replies, self._held_input = [], [], 0, []` and `self._typing_choice_other = False`.
5. `_begin_goal`, right after `self._reset_goal()`: `self._intake_mode = self.config.chat.intake`.
6. `_routed`, replace the final `else:` branch:

```python
        elif self._intake_mode == "conversation" and route.source not in ("forced", "fix_offer"):
            self._open_conversation(route.text)
        else:
            self._set_stage("intake")
            self._intake_job(route.text, check_prices=True)
```

7. `_input`: in the empty-text guard, add `*CONVERSATION_STAGES` to the tuple. Before `elif stage in GOAL_JOB_STAGES:` add:

```python
        elif stage == "conversation":
            self._conversation_input(text)
        elif stage == "conversing":
            self._held_input.append(text)
            self.console.print("[phil.muted]Got it; that goes with my next reply.[/]")
```

8. `_prompt`: add `if self.stage == "conversation" and self._typing_choice_other: return OTHER_PROMPT`.
9. `_build_decision`: first branch:

```python
        if stage == "conversation" and self._choices and not self._typing_choice_other:
            return conversation_decision(self._choices)
```

10. New methods, in the `goal → questions → plan` section after `_finish_questions`:

```python
    # --- the intake conversation (spec 2026-11-10) -------------------------------------------------

    def _open_conversation(self, text: str) -> None:
        self._turns = [ConverseTurn(speaker="user", text=text)]
        self._turn_job(check_prices=True)

    def _turn_job(self, *, must_decide: bool = False, check_prices: bool = False) -> None:
        """One conversation turn: the converse agent reads the base commit's snapshot and the turns so far."""
        self._set_stage("conversing")
        self._choices = []
        self._converse_calls += 1
        call, generation = self._converse_calls, self._generation
        self._step("conversing", generation)
        turns, route, overview = list(self._turns), self._route, self.overview
        route_depth = route.depth if route else None
        route_class = route.judgement.task_class if route and route.judgement else None
        detected = detect_test_cmd(self.info.root)  # reads only file names (and package.json's scripts)

        def fn(ctx: AgentContext) -> dict:
            if check_prices:
                self._note_unpriced(ctx)
            self._step("snapshot", generation)
            tree = self._snapshot(generation)
            self._step("conversing", generation)
            turn = converse(
                ctx, turns, tree=tree, overview=overview, route_depth=route_depth, route_class=route_class,
                detected_test_cmd=detected, must_decide=must_decide, call=call,
            )
            return {"turn": turn, "must_decide": must_decide}

        self._job("turn_ready", fn, failed="turn_failed")

    def _on_turn_ready(self, data: dict) -> None:
        turn: IntakeTurn = data["turn"]
        self.session.contract("intake_turn", turn)
        self._turns.append(ConverseTurn(speaker="phil", text=turn.reply, choices=turn.choices))
        self._recent.append(f"phil: {turn.reply[:TURN_CHARS]}")
        self.console.print(escape(turn.reply))
        if turn.ready:
            self._finish_conversation(turn)
            return
        self._choices = list(turn.choices)
        if self._choices and self.io.choose is None:
            for n, choice in enumerate(self._choices, 1):
                self.console.print(f"     {n}. {escape(_clip(choice))}")
            self.console.print("[phil.muted]     Type a number or your own answer; `go` plans with what I know.[/]")
        self._set_stage("conversation")

    def _conversation_input(self, text: str) -> None:
        """A reply in the conversation: a choice's number, "go", or the user's own words."""
        if self._typing_choice_other:
            self._typing_choice_other = False
            self._user_turn(text)
            return
        if text.lower() == PLAN_NOW:
            self._turn_job(must_decide=True)
            return
        token = text.rstrip(".)")
        if self._choices and token.isdigit():
            choice = int(token)
            if 1 <= choice <= len(self._choices):
                self._user_turn(self._choices[choice - 1])
                return
            if choice == len(self._choices) + 1 and self.io.choose is not None:
                self._typing_choice_other = True  # the menu's "Something else"
                return
        self._user_turn(text)  # any other text, a number that matches no choice included, is their answer

    def _user_turn(self, text: str) -> None:
        self._turns.append(ConverseTurn(speaker="user", text=text))
        self._recent.append(f"you: {text[:TURN_CHARS]}")
        self._user_replies += 1
        self._turn_job(must_decide=self._user_replies >= MAX_CONVERSATION_TURNS)

    def _finish_conversation(self, turn: IntakeTurn) -> None:
        """A ready turn: show its Plan block, then the quick run, the designer, or full planning."""
        goal = turn.goal
        assert goal is not None  # IntakeTurn guarantees it when ready
        self._goal = goal
        self.session.contract("goal", goal)
        self._safe_note("conversation_ready", depth=goal.depth, turns=len(self._turns))
        render_steps(self.console, turn.steps)
        if goal.depth == "quick":
            self.console.print(f"[phil.muted]{STARTING_QUICK}[/]")
            self._quick(goal)
        elif self._wants_design(goal):
            self._design_job(goal)
        else:
            self.console.print(f"[phil.muted]{PLANNING_IN_DETAIL}[/]")
            self._plan(goal, show_goal=False, announce=False)
```

11. `_plan`: add keyword `announce: bool = True` and print `"[phil.muted]Planning…[/]"` only when `announce` is true.
12. `_interrupt`: in the cancel branch's stage tuple add `*CONVERSATION_STAGES`.
13. `_command`: add before the final `else`:

```python
        elif command == "/go":
            if self.stage == "conversation":
                self._turn_job(must_decide=True)
            else:
                self.console.print("Nothing to plan right now: /go answers Phil's questions during intake.")
```

   and add `/go` to `HELP_INTRO`'s command list in the same style as its neighbours.
14. `_forced_input`: change `if stage in ("idle", "confirm_fix"):` to `if stage in ("idle", "confirm_fix", "conversation"):` so a forced message during a conversation starts that goal (Ruling: a forced prefix is an explicit new goal).

Pass the conversation to the architect and designer in Task 6; this task's architect/designer tests (`test_a_ready_full_goal_goes_to_the_architect_with_the_conversation`, `test_an_open_approach_goes_to_the_designer_with_the_conversation`) stay failing until then — mark them `@pytest.mark.xfail(strict=True, reason="Task 6")` now and remove the marks in Task 6.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat/test_conversation_flow.py`
Expected: PASS (2 xfailed). Then the whole suite: PASS.

- [ ] **Step 5: Commit**

Message: `Chat: intake is a conversation, turn by turn, with a Plan block before any work`. Stage the four source files and the new test file.

---

### Task 5: Limits, held messages and failures

**Files:**
- Modify: `src/phil/chat/controller.py`
- Test: `tests/chat/test_conversation_limits.py`

**Interfaces:**
- Consumes: Task 4's stages, `_turn_job`, `_on_turn_ready`, `_held_input`, `_user_replies`.
- Produces: `_on_turn_failed(data)`, `_conversation_fallback(reason: str)`, `_turn_budget_spent() -> bool`; constants `CONVERSATION_FALLBACK`, `BUDGET_DECIDES`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/chat/test_conversation_limits.py
from phil.config import PhilConfig
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
from tests.chat.test_conversation_flow import CONVERSATION, asks, payloads, quick_goal, ready
from tests.helpers import TEST_MODELS


def decided(factory):
    # The packet carries the contract as `model_dump_json(indent=2)`.
    return ['"must_decide": true' in p for p in payloads(factory, "converse")]


def test_go_plans_with_what_phil_knows(calc_repo):
    _, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "go"], {"converse": [asks(), ready(quick_goal())]}, config=CONVERSATION
    )
    assert decided(factory) == [False, True] and len(spawned) == 1


def test_the_go_command_plans_with_what_phil_knows(calc_repo):
    _, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "/go"], {"converse": [asks(), ready(quick_goal())]}, config=CONVERSATION
    )
    assert decided(factory) == [False, True]


def test_the_fourth_reply_must_decide(calc_repo):
    _, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "a", "b", "c", "d"],
        {"converse": [asks(), asks(), asks(), asks(), ready(quick_goal())]}, config=CONVERSATION,
    )
    assert decided(factory) == [False, False, False, False, True]


def test_not_ready_when_asked_to_decide_falls_back_to_classic_intake(calc_repo):
    text, spawned, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "in calc.py", "go", "y"],
        # The third turn is sent with must_decide (after "go") and still isn't ready.
        {"converse": [asks(), asks(), asks()], "intake": [goal()], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "classic intake" in text
    intake = payloads(factory, "intake")[0]
    assert "add subtract" in intake and "in calc.py" in intake
    assert len(spawned) == 1


def test_a_failed_turn_falls_back_to_classic_intake(calc_repo):
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "n"],
        {"converse": [RuntimeError("provider down")], "intake": [goal()], "architect": [plan()], "critic": [critique()]},
        config=CONVERSATION,
    )
    assert "provider down" in text and "classic intake" in text
    assert payloads(factory, "intake")


def test_a_spent_budget_makes_the_next_turn_decide(calc_repo):
    # Every scripted call costs $0.01 and the router's call counts too: route + turn 1 = $0.02,
    # past the $0.015 budget, so turn 2 must decide.
    config = PhilConfig(models=TEST_MODELS, chat={"intake": "conversation", "max_cost_usd": 0.015})
    text, _, _, factory, _ = run_chat(
        calc_repo, ["add subtract", "calc.py"],
        {"converse": [asks(), ready(quick_goal())]}, config=config, usage=(100, 20, 0.01),
    )
    assert decided(factory) == [False, True]
    assert "planning budget is spent" in text


def opening(seen, text="add subtract"):
    """The chat's first message, which also keeps the controller for scripted turns to type into.
    Jobs run inline (the default `io.submit`), so a turn's scripted callable runs while the chat is
    in the `conversing` stage: input it sends is "typed while a turn is in flight"."""

    def item(controller):
        seen["controller"] = controller
        return text

    return item


def typing(seen, text, then):
    """A scripted converse turn during which the user types `text`; the turn then returns `then`."""

    def turn(scripted_turn):
        seen["controller"]._input(text)
        return then

    return turn


def test_a_message_typed_during_a_turn_joins_the_next_turn(calc_repo):
    seen = {}
    _, spawned, _, factory, _ = run_chat(
        calc_repo, [opening(seen), "calc.py"],
        {"converse": [typing(seen, "also handle negatives", asks()), asks(), ready(quick_goal())]},
        config=CONVERSATION,
    )
    second, third = payloads(factory, "converse")[1:]
    assert "also handle negatives" in second and "calc.py" not in second
    assert third.index("also handle negatives") < third.index("calc.py")
    assert len(spawned) == 1


def test_a_message_held_past_a_ready_turn_continues_the_conversation(calc_repo):
    seen = {}
    text, spawned, _, factory, _ = run_chat(
        calc_repo, [opening(seen)],
        {"converse": [typing(seen, "and multiply", ready(quick_goal())), ready(quick_goal(), reply="Adding both.")]},
        config=CONVERSATION,
    )
    assert "and multiply" in payloads(factory, "converse")[1]
    assert text.count("Plan:") == 1 and "Adding both." in text  # only the second ready turn launches
    assert len(spawned) == 1


def test_ctrl_c_during_a_turn_cancels_the_goal(calc_repo):
    seen = {}

    def interrupted(scripted_turn):
        seen["controller"]._interrupt()
        return ready(quick_goal())  # lands after the cancel: stale, dropped

    text, spawned, _, _, _ = run_chat(
        calc_repo, [opening(seen)], {"converse": [interrupted]}, config=CONVERSATION
    )
    assert "Cancelled the current goal." in text and "Plan:" not in text and spawned == []
```

`run_chat` takes no `usage` keyword today: add `usage=None` to its signature and build the factory with `ChatFactory(scripts, **({"usage": usage} if usage else {}))` (a test-helper change).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat/test_conversation_limits.py`
Expected: FAIL — the fallback, budget, and held-message tests (the go / cap / Ctrl-C ones may already pass from Task 4; they stay as regression tests).

- [ ] **Step 3: Implement**

Constants:

```python
CONVERSATION_FALLBACK = "Continuing with the classic intake."
BUDGET_DECIDES = "This goal's planning budget is spent (chat.max_cost_usd): planning with what I know."
```

`_turn_job`: at the top, after `self._set_stage("conversing")`:

```python
        if not must_decide and self._turn_budget_spent():
            must_decide = True
            self.console.print(f"[phil.warn]{BUDGET_DECIDES}[/]")
        self._budget_note()
```

```python
    def _turn_budget_spent(self) -> bool:
        return self._goal_cost() >= self.config.chat.max_cost_usd
```

`_on_turn_ready` gains the must-decide check and the held messages; the whole method becomes:

```python
    def _on_turn_ready(self, data: dict) -> None:
        turn: IntakeTurn = data["turn"]
        if data["must_decide"] and not turn.ready:
            self._conversation_fallback("the conversation didn't decide when asked to")
            return
        self.session.contract("intake_turn", turn)
        self._turns.append(ConverseTurn(speaker="phil", text=turn.reply, choices=turn.choices))
        self._recent.append(f"phil: {turn.reply[:TURN_CHARS]}")
        self.console.print(escape(turn.reply))
        held, self._held_input = self._held_input, []
        if turn.ready and not held:
            self._finish_conversation(turn)
            return
        if held:
            # The user typed while this turn was in flight: they come first, even over a ready turn.
            for text in held[:-1]:
                self._turns.append(ConverseTurn(speaker="user", text=text))
            self._user_turn(held[-1])  # one more turn carries every held message, in order
            return
        self._choices = list(turn.choices)
        if self._choices and self.io.choose is None:
            for n, choice in enumerate(self._choices, 1):
                self.console.print(f"     {n}. {escape(_clip(choice))}")
            self.console.print("[phil.muted]     Type a number or your own answer; `go` plans with what I know.[/]")
        self._set_stage("conversation")
```

(`_user_turn` adds the last held message, bumps `_user_replies` once, and starts the turn; the earlier held messages are added as their own user turns first.)

New methods:

```python
    def _on_turn_failed(self, data: dict) -> None:
        self._failed(data["error"], data.get("failure"))
        self._conversation_fallback(data["error"])

    def _conversation_fallback(self, reason: str) -> None:
        """A turn failed: the user's messages so far go once to the classic intake (spec 2026-11-10 §4.7)."""
        message = "\n\n".join(turn.text for turn in self._turns if turn.speaker == "user")
        self._safe_note("conversation_fallback", reason=reason)
        self.console.print(f"[phil.muted]{CONVERSATION_FALLBACK}[/]")
        self._intake_mode = "classic"
        self._goal_text = message
        self._set_stage("intake")
        self._intake_job(message)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat/test_conversation_limits.py tests/chat/test_conversation_flow.py`
Expected: PASS. Then the whole suite: PASS.

- [ ] **Step 5: Commit**

Message: `Chat: the conversation's limits, held messages and fallback to classic intake`. Stage the controller, the new test file, and `tests/chat/test_controller.py` if `run_chat` changed.

---

### Task 6: Saved conversations, and the architect and designer hear it

**Files:**
- Modify: `src/phil/chat/controller.py` (`_save`, `_reopen`, `_plan`, `_edit`, `_design_job`), `src/phil/chat/planning.py` (`Planner.draft` / `revise` / `_cycle` / `_architect`), `src/phil/chat/design.py` (`propose_approaches`), `src/phil/prompts/architect.md`, `src/phil/prompts/design.md`
- Test: `tests/chat/test_conversation_flow.py` (remove the xfail marks), `tests/chat/test_conversation_reopen.py`, `tests/chat/test_planning.py`

**Interfaces:**
- Consumes: Task 4/5 controller attributes.
- Produces: `Planner.draft(..., conversation: Sequence[ConverseTurn] | None = None)`, `Planner.revise(..., conversation=None)`, `propose_approaches(..., conversation: Sequence[ConverseTurn] | None = None)`; `state.json` keys `"conversation"` (list of `ConverseTurn` dumps), `"choices"`, `"user_replies"`, `"intake_mode"`, and counter `"converse"`.

- [ ] **Step 1: Write the failing tests**

Remove the two `xfail` marks from Task 4. Add to `tests/chat/test_planning.py`:

```python
def test_the_architect_sees_the_conversation(chat_ctx, tmp_path):
    from phil.chat.planning import Planner
    from phil.contracts import ConverseTurn

    factory = ScriptedAgentFactory({"architect": [plan()], "critic": [critique()]})
    turns = [ConverseTurn(speaker="user", text="use argparse please")]
    Planner(chat_ctx(factory), "calc.py").draft(goal(), tmp_path, conversation=turns)
    assert "use argparse please" in str(factory.calls[0][1])
```

(Use the module's existing imports for `ScriptedAgentFactory`, `plan`, `critique`, `goal`.)

```python
# tests/chat/test_conversation_reopen.py
import json

from tests.chat.test_controller import run_chat, session_dir
from tests.chat.test_conversation_flow import CONVERSATION, asks, payloads, quick_goal, ready
from tests.chat.test_reopen_state import reopen


def test_a_reopened_chat_resumes_the_conversation(calc_repo):
    run_chat(calc_repo, ["add subtract"], {"converse": [asks("Which module?")]}, config=CONVERSATION)  # EOF
    text, spawned, _, factory, _ = reopen(
        calc_repo, ["calc.py"], {"converse": [ready(quick_goal())]}, config=CONVERSATION
    )
    assert "Which module?" in text  # Phil's last reply is shown again
    resumed = payloads(factory, "converse")[0]
    assert "add subtract" in resumed and "calc.py" in resumed
    assert len(spawned) == 1


def test_a_chat_closed_mid_turn_resends_the_unanswered_turn(calc_repo):
    run_chat(calc_repo, ["add subtract"], {"converse": [asks("Which module?")]}, config=CONVERSATION)
    path = session_dir(calc_repo) / "state.json"
    state = json.loads(path.read_text())
    # As if the chat closed while the first turn was in flight: the user's message is the last turn.
    state["stage"] = "conversation"
    state["conversation"] = [{"speaker": "user", "text": "add subtract", "choices": []}]
    path.write_text(json.dumps(state))
    _, spawned, _, factory, _ = reopen(calc_repo, [], {"converse": [ready(quick_goal())]}, config=CONVERSATION)
    calls = payloads(factory, "converse")
    assert len(calls) == 1 and "add subtract" in calls[0]
    assert len(spawned) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat/test_conversation_flow.py tests/chat/test_conversation_reopen.py tests/chat/test_planning.py`
Expected: FAIL — `draft() got an unexpected keyword argument 'conversation'`, the conversation missing from the architect/designer payloads, and the reopen tests.

- [ ] **Step 3: Implement**

`planning.py`: thread `conversation: Sequence[ConverseTurn] | None = None` through `draft`, `revise`, `_cycle` and `_architect` exactly as `approach_note` is threaded, and in `_architect` pass `conversation=list(conversation) if conversation else None` to `ArchitectInput`.

`design.py`: `propose_approaches(..., conversation: Sequence[ConverseTurn] | None = None)` → `DesignInput(goal=goal, repo_overview=overview, conversation=list(conversation) if conversation else None)`.

Controller: in `_plan` and `_edit` capture `conversation = list(self._turns) or None` on the main thread with the other captured values and pass `conversation=conversation` to `self.planner.draft` / `self.planner.revise`; in `_design_job` capture it the same way and pass it to `propose_approaches`.

Prompts — append to `src/phil/prompts/architect.md` and `src/phil/prompts/design.md`:

```markdown
## The conversation
- When `conversation` is set, it is what the user and Phil said before planning, oldest first. `goal` summarises it; where they differ, the user's own words win.
```

`_save`: when `self.stage == "conversing"` save the stage as `"conversation"`; add keys

```python
                    "conversation": [turn.model_dump(mode="json") for turn in self._turns],
                    "choices": self._choices,
                    "user_replies": self._user_replies,
                    "intake_mode": self._intake_mode,
```

and `"converse": self._converse_calls` to `"counters"`.

`_reopen`: read the counter `"converse"` with the others (add it to the counters key tuple and `self._converse_calls = max(...)`). Parse the turns:

```python
        try:
            turns = [ConverseTurn.model_validate(t) for t in saved.get("conversation") or []]
        except Exception:
            turns = []  # a bad conversation falls back alone, like a bad approach
```

and before `elif stage in ("approval", "planning", "edit") ...` add:

```python
        elif stage == "conversation" and turns:
            self._turns, self._intake_mode = turns, "conversation"
            self._goal_text = turns[0].text
            replies = _count(saved.get("user_replies"))
            self._user_replies = replies or 0
            last = turns[-1]
            if last.speaker == "user":  # closed mid-turn: send it again
                self._turn_job()
            else:
                self.console.print(escape(last.text))
                self._choices = [c for c in saved.get("choices") or [] if isinstance(c, str)]
                self._set_stage("conversation")
```

(`self._goal_mark` is already reset on reopen, so the budget starts afresh, as for a reopened plan.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q tests/chat`
Expected: PASS. Then the whole suite: PASS.

- [ ] **Step 5: Commit**

Message: `Chat: a reopened chat resumes its conversation; the architect and designer hear it`. Stage the source, prompt and test files.

---

### Task 7: Guide

**Files:**
- Modify: `docs/guide.md` (Usage, Routing, the `[chat]` configuration and Observability's planning-budget paragraph)

- [ ] **Step 1: Write the docs**

- In **Usage** (the "One chat follows one goal at a time" paragraph): intake is now a short conversation. Phil replies in a line or two; when something only you can supply is missing it asks, with numbered choices when there are a few clear options (type a number, your own answer, or `go` / `/go` to plan with what it knows). Before anything runs it shows a **Plan**: the steps it will take. Small, clear requests start right after; larger ones go on to design proposals, the architect's plan and your approval, as before. Phil may read the repo, read-only, to make its questions specific. After 4 replies it decides with what it knows.
- In **Routing**: the router's depth is now a hint for the conversation, which may make a quick request full or the other way round; `/ask`, `/quick`, `/full` and `/full!` skip the conversation.
- In the `[chat]` configuration block: `intake = "conversation"` (default) or `"classic"` (the one-call intake with question menus, as before 2026-11).
- In **Observability**: the planning budget covers the conversation's turns too; when it's spent, Phil plans with what it knows.

- [ ] **Step 2: Check**

Run: `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q` (some tests check the guide's config keys; if one lists every `[chat]` key, it now expects `intake`).
Expected: PASS.

- [ ] **Step 3: Commit**

Message: `Guide: intake as a conversation, and [chat] intake`. Stage `docs/guide.md`.

---

## Live acceptance (user-run, after the branch is green)

1. A clear small request ("fix the typo in the README heading" in a repo with one) starts after one turn, with a Plan block.
2. A vague request ("add a newsletter signup") gets a question with choices, then a Plan block, then the architect.
3. `/go` during the conversation plans at once.
4. `phil --set chat.intake=classic chat` behaves exactly as before.
