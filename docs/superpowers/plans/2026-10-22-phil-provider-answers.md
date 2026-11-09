# Answers That Work on Every Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents return their answers on Anthropic, OpenAI, Google, Ollama and custom endpoints (current models included), and provider errors say what to do.

**Architecture:** Every chat model Phil builds turns a forced tool choice into `"auto"` (a `bind_tools` override, applied in `build_chat_model`). A new `AnswerReminder` middleware on every agent accepts a valid JSON text answer or reminds the model (at most twice), replacing `end_on_text`. Real OpenAI uses the Responses API. Failure classification learns the provider's resolved spec (tagged on the exception by `invoke_agent`), local servers that are down, Google's bad-key 400, and quota-before-busy. Setup gets current suggestions and marks Ollama models that can't call tools.

**Tech Stack:** Python 3.14, LangChain 1.4 (`create_agent`, `AgentMiddleware`), deepagents, langchain-openai / -anthropic / -google-genai / -openrouter, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-22-phil-provider-answers-design.md`

## Global Constraints

- Never run `pytest -m live` or `-m bench`. Tests use `PHIL_HOME` set to a temp dir (the suite's conftest already does).
- Edit files only with Edit or Write; no sed/python/heredoc edits of repo files.
- Commit by writing the message to `.git/phil-commit-msg.txt` with Write, then `git commit -F .git/phil-commit-msg.txt`. Every message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage only this task's files.
- Importing `phil.agents.providers` must not load LangChain or any provider SDK (`tests/agents/test_providers.py::test_importing_providers_does_not_load_llm_stack`).
- The reminder text is exactly `Return your answer by calling the <ToolName> tool.`, where `<ToolName>` is the output contract's class name. At most **2** reminders per agent attempt.
- The check line for a pass that needed a reminder ends with ` (needed a reminder)`.
- The local-server action is exactly ``Is `ollama serve` running?`` for the provider named `ollama` and `Is the server at <base_url> running?` otherwise; the headline is `Couldn't reach <provider> at <base_url>.`
- Loopback hosts: `localhost`, `127.0.0.1`, `::1`.
- Setup suggestions: Anthropic `anthropic:claude-opus-5-5` / `anthropic:claude-haiku-5-5`; Google `google:gemini-4-argon` / `google:gemini-3.8-flash`; OpenAI and OpenRouter unchanged.
- The Ollama marker label suffix is exactly `  — can't call tools, won't work with Phil`.

## Review Focus

1. A text answer whose JSON is valid but carries an extra prose sentence before or after a fence ("Here it is: ```json {...}``` Hope that helps") must be accepted, not reminded: the fence is the answer.
2. A model that answers in text on a **light** agent near its call cap: the reminder must not push it past `max_model_calls + BUDGET_GRACE_CALLS`; the hard stop still ends the loop.
3. A deep agent whose model narrates progress mid-task in plain text ("I'll now read the tests") gets a reminder and keeps working, rather than ending the attempt.
4. A run worker failing on a custom provider's 401 must name that provider's own key variable (the run path has no `provider=` argument today).
5. An OpenAI `APITimeoutError` (which subclasses `APIConnectionError`) to a localhost server is still a retried timeout, not "server isn't running".

Each line has its test in the owning task (Task 2: 1, 2, 3; Task 5: 4, 5).

---

### Task 1: No forced tool call

**Files:**
- Create: `src/phil/agents/tool_choice.py`
- Modify: `src/phil/agents/providers.py` (the `openai`, `anthropic`, `google` branches of `build_chat_model`)
- Modify: `src/phil/agents/openrouter_model.py` (`PhilChatOpenRouter` bases)
- Test: `tests/agents/test_tool_choice.py` (new), `tests/agents/test_providers.py` (type-name asserts)

**Interfaces:**
- Produces: `unforced_choice(tool_choice: Any) -> Any`; `class NoForcedToolChoice` (mixin overriding `bind_tools(self, tools, *, tool_choice=None, **kwargs)`); `without_forced_tool_choice(cls: type) -> type` (cached subclass named `Phil<cls.__name__>`).

- [ ] **Step 1: Write the failing tests**

`tests/agents/test_tool_choice.py`:

```python
import pytest
from pydantic import BaseModel

from phil.agents.providers import BUILTIN_PROVIDERS, ProviderSpec, build_chat_model
from phil.agents.tool_choice import unforced_choice

FAKE_ENVIRON = {
    "OPENROUTER_API_KEY": "dummy-openrouter",
    "OPENAI_API_KEY": "dummy-openai",
    "ANTHROPIC_API_KEY": "dummy-anthropic",
    "GOOGLE_API_KEY": "dummy-google",
}


class Echo(BaseModel):
    """Echo a word back."""

    word: str


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ("any", "auto"),
        ("required", "auto"),
        (True, "auto"),
        ("Echo", "auto"),
        ({"type": "tool", "name": "Echo"}, "auto"),
        ({"type": "function", "function": {"name": "Echo"}}, "auto"),
        ({"type": "any"}, "auto"),
        (None, None),
        (False, False),
        ("auto", "auto"),
        ("none", "none"),
        ({"type": "auto"}, {"type": "auto"}),
    ],
)
def test_unforced_choice(given, sent):
    assert unforced_choice(given) == sent


SPECS = {
    "openai": BUILTIN_PROVIDERS["openai"],
    "ollama": BUILTIN_PROVIDERS["ollama"],
    "custom": ProviderSpec("lab", "openai", "http://lab:8000/v1", None, None, None),
    "anthropic": BUILTIN_PROVIDERS["anthropic"],
    "google": BUILTIN_PROVIDERS["google"],
    "openrouter": BUILTIN_PROVIDERS["openrouter"],
}


@pytest.mark.parametrize("kind", sorted(SPECS))
@pytest.mark.parametrize("forced", ["any", "required", "Echo"])
def test_every_provider_kind_sends_auto_instead_of_a_forced_choice(kind, forced):
    model = build_chat_model(SPECS[kind], "some-model", 30, environ=FAKE_ENVIRON)
    assert model.bind_tools([Echo], tool_choice=forced).kwargs == model.bind_tools([Echo], tool_choice="auto").kwargs


@pytest.mark.parametrize("kind", sorted(SPECS))
def test_an_unforced_choice_passes_through(kind):
    model = build_chat_model(SPECS[kind], "some-model", 30, environ=FAKE_ENVIRON)
    base = type(model).__mro__[2]  # Phil<Base> -> NoForcedToolChoice -> <Base>
    plain = base.bind_tools(model, [Echo], tool_choice="auto")
    assert model.bind_tools([Echo], tool_choice="auto").kwargs == plain.kwargs
```

In `tests/agents/test_providers.py`, the construction tests assert the exact class name; with Phil's subclasses they must assert the base class instead. Replace each:
- `assert type(model).__name__ == "ChatOpenAI"` (two places) → `from langchain_openai import ChatOpenAI` inside the test and `assert isinstance(model, ChatOpenAI)`
- `assert type(model).__name__ == "ChatAnthropic"` → `from langchain_anthropic import ChatAnthropic` and `assert isinstance(model, ChatAnthropic)`
- `assert type(model).__name__ == "ChatGoogleGenerativeAI"` → `from langchain_google_genai import ChatGoogleGenerativeAI` and `assert isinstance(model, ChatGoogleGenerativeAI)`

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/agents/test_tool_choice.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'phil.agents.tool_choice'`.

- [ ] **Step 3: Implement**

`src/phil/agents/tool_choice.py`:

```python
"""Phil's chat models never force a tool call.

LangChain's `create_agent` binds an agent's structured-output tool with `tool_choice="any"`
whenever a `ToolStrategy` is used, with no option to turn it off. Current Claude models (Opus 5.5,
Sonnet 5.5, Fable 5.1) reject a forced tool choice with a 400, and Ollama ignores it. So every
model Phil builds offers its tools unforced; an agent that answers in text instead is handled by
`phil.agents.reminder`. Imports nothing heavy: providers.py imports it at module level.
"""

from functools import cache
from typing import Any


def unforced_choice(tool_choice: Any) -> Any:
    """`tool_choice` without forcing: "any", "required", True, a tool's name, or a dict that
    forces one become "auto"; None, False, "auto", "none" and an auto/none dict pass through."""
    if tool_choice is None or tool_choice is False:
        return tool_choice
    if isinstance(tool_choice, str) and tool_choice in ("auto", "none"):
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") in ("auto", "none"):
        return tool_choice
    return "auto"


class NoForcedToolChoice:
    """Mixin for a LangChain chat model: `bind_tools` never forces a tool call."""

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return super().bind_tools(tools, tool_choice=unforced_choice(tool_choice), **kwargs)  # type: ignore[misc]


@cache
def without_forced_tool_choice(cls: type) -> type:
    """`cls` with `NoForcedToolChoice` in front, named `Phil<cls.__name__>`."""
    return type(f"Phil{cls.__name__}", (NoForcedToolChoice, cls), {"__module__": __name__})
```

In `src/phil/agents/providers.py`, add `from phil.agents.tool_choice import without_forced_tool_choice` to the imports, and change the three branches of `build_chat_model`:

```python
    if spec.kind == "openai":
        from langchain_openai import ChatOpenAI

        return without_forced_tool_choice(ChatOpenAI)(
            model=model_name, base_url=spec.base_url, api_key=key, timeout=timeout_s, max_retries=0
        )
    if spec.kind == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return without_forced_tool_choice(ChatAnthropic)(
            model=model_name, base_url=spec.base_url, api_key=key, timeout=timeout_s, max_retries=0
        )
    if spec.kind == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # The Google SDK reads max_retries=0 as "use its default" (5 retries); 1 is a single attempt.
        return without_forced_tool_choice(ChatGoogleGenerativeAI)(
            model=model_name, base_url=spec.base_url, google_api_key=key, timeout=timeout_s, max_retries=1
        )
```

Update `build_chat_model`'s docstring: add the sentence "Every model offers its tools unforced (`phil.agents.tool_choice`)."

In `src/phil/agents/openrouter_model.py`, import the mixin and put it first:

```python
from phil.agents.tool_choice import NoForcedToolChoice


class PhilChatOpenRouter(NoForcedToolChoice, ChatOpenRouter):
```

and add to the module docstring: "It also never forces a tool call (`phil.agents.tool_choice`)."

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/agents/test_tool_choice.py tests/agents/test_providers.py -q`
Expected: PASS. (`__mro__[2]` is the SDK class for both shapes: `Phil<Base>, NoForcedToolChoice, <Base>` and `PhilChatOpenRouter, NoForcedToolChoice, ChatOpenRouter`.)

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/tool_choice.py src/phil/agents/providers.py src/phil/agents/openrouter_model.py tests/agents/test_tool_choice.py tests/agents/test_providers.py
git commit -F .git/phil-commit-msg.txt   # "Providers: never force a tool call"
```

---

### Task 2: A text answer gets a second chance

**Files:**
- Create: `src/phil/agents/reminder.py`
- Modify: `src/phil/agents/factory.py` (install the middleware in all three builders; delete `_end_on_text_middleware`)
- Modify: `src/phil/agents/spec.py` (delete `end_on_text` and its comment)
- Modify: `src/phil/agents/registry.py` (drop `end_on_text=True` from intake, critic, reviewer, route, memory)
- Modify: `src/phil/agents/check.py` (drop `end_on_text=True` from `MODEL_CHECK`, update its comment)
- Modify: `src/phil/store/telemetry.py` (`TelemetryRow.reminders: int = 0`), `src/phil/store/db.py` (migration)
- Modify: `src/phil/agents/invoke.py` (record `reminders`)
- Test: `tests/agents/test_reminder.py` (new), `tests/agents/test_prose_answers.py`, `tests/agents/test_factory.py:124`

**Interfaces:**
- Consumes: Task 1's unforced models (no code dependency; the fakes ignore tool choice).
- Produces: `phil.agents.reminder.MAX_REMINDERS = 2`, `REMINDER_ID = "phil-reminder"`, `reminder_text(tool_name: str) -> str`, `json_answer(text: str, contract: type[Contract]) -> Contract | None`, `reminder_count(messages: list) -> int`, `answer_reminder_middleware(contract: type[Contract]) -> AgentMiddleware` (class name `AnswerReminder`). Telemetry rows gain `reminders`.

- [ ] **Step 1: Write the failing tests**

`tests/agents/test_reminder.py`:

```python
import json

import pytest
from langchain_core.messages import AIMessage

from phil.agents.factory import BUDGET_GRACE_CALLS
from phil.agents.invoke import AgentContext, ContractViolation, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.reminder import MAX_REMINDERS, json_answer, reminder_text
from phil.contracts import CriticInput, Goal, Plan, PlanCritique, Task
from phil.packets import build_packet
from phil.store.artifacts import ArtifactStore
from tests.agents.conftest import critique
from tests.agents.test_model_retry import ScriptedChatModel, architect_packet, real_factory, tool_call, usage


class RecordingModel(ScriptedChatModel):
    """A ScriptedChatModel that also keeps the last human message each call received."""

    seen: list[str] = []

    def model_post_init(self, _context) -> None:
        super().model_post_init(_context)
        self.seen = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        humans = [m for m in messages if getattr(m, "type", None) == "human"]
        self.seen.append(str(humans[-1].content) if humans else "")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def text(content: str) -> AIMessage:
    return usage(AIMessage(content=content))


def _critic_packet():
    task = Task(id="CALC-001", description="Add subtract", acceptance_criteria=["subtract(3, 1) == 2"])
    plan = Plan(keyword="CALC", description="Add subtract", tasks=[task])
    return build_packet("critic", CriticInput(goal=Goal(objective="Add subtract"), plan=plan), budget_tokens=8000)


def _ctx(config, conn, tmp_path, monkeypatch, model, workdir=None):
    return AgentContext(
        config=config, conn=conn, layer="chat", artifacts=ArtifactStore(tmp_path / "run"),
        workdir=workdir, factory=real_factory(model, monkeypatch),
    )


# --- json_answer -------------------------------------------------------------------------------

CRITIQUE = critique().model_dump(mode="json")


def test_a_bare_json_answer_validates():
    assert json_answer(json.dumps(CRITIQUE), PlanCritique) == critique()


def test_a_fenced_json_answer_validates():
    assert json_answer(f"```json\n{json.dumps(CRITIQUE)}\n```", PlanCritique) == critique()


def test_a_fence_wrapped_in_prose_validates():
    reply = f"Here it is:\n```json\n{json.dumps(CRITIQUE)}\n```\nHope that helps."
    assert json_answer(reply, PlanCritique) == critique()


@pytest.mark.parametrize(
    "reply",
    [
        "I think the plan is fine.",
        "{not json}",
        json.dumps({"verdict": "maybe"}),
        json.dumps([CRITIQUE]),
        f"```json\n{json.dumps(CRITIQUE)}\n```\n```json\n{json.dumps(CRITIQUE)}\n```",
    ],
)
def test_anything_else_is_not_an_answer(reply):
    assert json_answer(reply, PlanCritique) is None


def test_the_reminder_names_the_answer_tool():
    assert reminder_text("PlanCritique") == "Return your answer by calling the PlanCritique tool."


# --- the middleware, through invoke_agent ----------------------------------------------------


def test_a_lean_agent_takes_a_fenced_json_text_answer(config, conn, tmp_path, monkeypatch):
    model = RecordingModel(script=[text(f"```json\n{json.dumps(CRITIQUE)}\n```")])
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model)
    assert invoke_agent(get_spec("critic"), _critic_packet(), ctx, node="critic") == critique()
    assert len(model.received) == 1
    assert [tuple(r) for r in conn.execute("SELECT outcome, reminders FROM telemetry")] == [("ok", 0)]


def test_a_reminder_then_a_tool_call_succeeds(config, conn, tmp_path, monkeypatch):
    model = RecordingModel(script=[text("The plan looks fine to me."), tool_call("PlanCritique", CRITIQUE, "c1")])
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model)
    assert invoke_agent(get_spec("critic"), _critic_packet(), ctx, node="critic") == critique()
    assert model.seen[1] == reminder_text("PlanCritique")
    assert [tuple(r) for r in conn.execute("SELECT outcome, reminders FROM telemetry")] == [("ok", 1)]


def test_two_reminders_then_text_ends_the_attempt(config, conn, tmp_path, monkeypatch):
    model = RecordingModel(script=[text(f"Prose {i}.") for i in range(1, 7)])
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model)
    with pytest.raises(ContractViolation) as excinfo:
        invoke_agent(get_spec("critic"), _critic_packet(), ctx, node="critic")
    assert excinfo.value.problems == ["no structured output was returned"]
    assert len(model.received) == 2 * (1 + MAX_REMINDERS)  # two attempts, each: answer + 2 reminders
    rows = [tuple(r) for r in conn.execute("SELECT outcome, reminders FROM telemetry ORDER BY id")]
    assert rows == [("invalid", MAX_REMINDERS), ("invalid", MAX_REMINDERS)]
    rejected = json.loads((tmp_path / "run" / "outputs" / "critic-run-1.rejected.json").read_text())
    assert rejected["raw"] == "Prose 3."


def test_a_deep_agent_narrating_in_text_is_reminded_and_keeps_working(config, conn, tmp_path, monkeypatch):
    from tests.agents.test_model_retry import PLAN_ARGS

    workdir = tmp_path / "repo"
    workdir.mkdir()
    (workdir / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    model = RecordingModel(
        script=[text("I'll now read calc.py."), tool_call("ls", {"path": "/"}, "c1"), tool_call("Plan", PLAN_ARGS, "c2")]
    )
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model, workdir=workdir)
    plan = invoke_agent(get_spec("architect"), architect_packet(), ctx, node="architect")
    assert plan.keyword == PLAN_ARGS["keyword"]
    assert model.seen[1] == reminder_text("Plan")


def test_reminders_never_take_a_light_agent_past_its_hard_stop(config, conn, tmp_path, monkeypatch):
    spec = get_spec("answer")
    hard_stop = spec.max_model_calls + BUDGET_GRACE_CALLS
    model = RecordingModel(
        script=[tool_call("ls", {"path": "/"}, f"c{i}") for i in range(spec.max_model_calls - 1)]
        + [text("Still looking.") for _ in range(10)]
    )
    workdir = tmp_path / "repo"
    workdir.mkdir()
    ctx = _ctx(config, conn, tmp_path, monkeypatch, model, workdir=workdir)
    from phil.contracts.routing import AnswerInput

    packet = build_packet("answer", AnswerInput(question="What does calc do?"), budget_tokens=4000)
    with pytest.raises(ContractViolation):
        invoke_agent(spec, packet, ctx, node="answer", max_attempts=1)
    assert len(model.received) <= hard_stop
```

Notes for the implementer:
- `PLAN_ARGS` and `architect_packet` already exist in `tests/agents/test_model_retry.py`; `critique()` in `tests/agents/conftest.py`. If `AnswerInput` takes different fields, build it the way `tests/agents/test_light_harness.py` does.
- `tests/agents/test_prose_answers.py` asserts the old one-call-per-attempt behaviour. Change it:
  - delete `test_every_lean_production_spec_ends_on_text`, and add in its place:
    ```python
    def test_no_spec_has_end_on_text_any_more():
        assert all(not hasattr(spec, "end_on_text") for spec in SPECS.values())
    ```
  - in `test_a_lean_agent_answering_in_prose_makes_one_call_per_attempt` (rename to `test_a_lean_agent_answering_in_prose_is_reminded_then_retried`): `assert len(model.received) == 6` (two attempts of answer + 2 reminders), keep the retry-message assert but check it on attempt 2's first call (`model.received[3] == model.received[0] + 1`), and the rejected `raw` values become `"Here is my answer in prose (3)."` and `"Here is my answer in prose (6)."`.
  - in `test_a_deep_agent_answering_in_prose_makes_one_call_per_attempt` (rename to `..._is_reminded_then_retried`): `assert len(model.received) == 6` and `rejected["raw"] == "Here is my answer in prose (3)."`.
  - update the module docstring: a prose answer gets up to two reminders per attempt, then the attempt ends with the last text kept as `raw`.
- `tests/agents/test_factory.py:124`: the lean agent's middleware becomes `["PhilModelRetryMiddleware", "AnswerReminder"]`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/agents/test_reminder.py tests/agents/test_prose_answers.py tests/agents/test_factory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'phil.agents.reminder'`.

- [ ] **Step 3: Implement**

`src/phil/agents/reminder.py`:

```python
"""A model that answers in text instead of calling its answer tool gets a second chance.

Phil's models never force the answer tool (phil.agents.tool_choice), so a model can end its turn
in plain text. After each model call: text that is the answer, one JSON object, bare or in one
``` fence, valid for the contract, is taken as the structured response; any other text gets a
reminder to call the tool, at most MAX_REMINDERS times per attempt; then the turn ends with no
answer, and invoke_agent's contract retry takes over (spec 2026-10-22 §4.2).
"""

import json
import re
from typing import Any

from pydantic import ValidationError

from phil.contracts import Contract

MAX_REMINDERS = 2
REMINDER_ID = "phil-reminder"  # message ids, so reminders can be counted; never sent to providers
_FENCE = re.compile(r"```[a-zA-Z]*[ \t]*\n?(.*?)```", re.DOTALL)


def reminder_text(tool_name: str) -> str:
    return f"Return your answer by calling the {tool_name} tool."


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(str(part.get("text", "")))
    return "".join(parts)


def json_answer(text: str, contract: type[Contract]) -> Contract | None:
    """The contract `text` holds as one JSON object, bare or inside exactly one ``` fence, or None."""
    fences = _FENCE.findall(text)
    if len(fences) > 1:
        return None
    candidate = (fences[0] if fences else text).strip()
    if not candidate.startswith("{"):
        return None
    try:
        data = json.loads(candidate)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return contract.model_validate(data)
    except ValidationError:
        return None


def reminder_count(messages: list) -> int:
    return sum(
        1
        for m in messages
        if getattr(m, "type", None) == "human" and str(getattr(m, "id", None) or "").startswith(REMINDER_ID)
    )


def answer_reminder_middleware(contract: type[Contract]) -> Any:
    from langchain.agents.middleware import AgentMiddleware, hook_config
    from langchain_core.messages import HumanMessage

    tool_name = contract.__name__

    def after(state: Any) -> dict[str, Any] | None:
        if state.get("structured_response") is not None:
            return None
        messages = state["messages"]
        last = messages[-1] if messages else None
        if getattr(last, "type", None) != "ai" or getattr(last, "tool_calls", None):
            return None
        answer = json_answer(_text(last.content), contract)
        if answer is not None:
            return {"structured_response": answer, "jump_to": "end"}
        sent = reminder_count(messages)
        if sent >= MAX_REMINDERS:
            return {"jump_to": "end"}
        reminder = HumanMessage(reminder_text(tool_name), id=f"{REMINDER_ID}-{sent + 1}")
        return {"messages": [reminder], "jump_to": "model"}

    class AnswerReminder(AgentMiddleware):
        @hook_config(can_jump_to=["model", "end"])
        def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return after(state)

        @hook_config(can_jump_to=["model", "end"])
        async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return after(state)

    return AnswerReminder()
```

In `src/phil/agents/factory.py`:
- import: `from phil.agents.reminder import answer_reminder_middleware`
- `_build_light_agent`: after the call-budget append, add `middleware.append(answer_reminder_middleware(spec.out_contract))`.
- `_build_lean_agent`: replace the `end_on_text` block with `middleware: list[Any] = [PhilModelRetryMiddleware(), answer_reminder_middleware(spec.out_contract)]`.
- `_build_deep_agent`: after the call-budget append, add `middleware.append(answer_reminder_middleware(spec.out_contract))`.
- delete `_end_on_text_middleware`.
- `_call_budget_middleware`'s docstring: replace "and so is a plain-text one" with "and a plain-text one gets a reminder (phil.agents.reminder)".

In `src/phil/agents/spec.py`, delete the `end_on_text` field and its four comment lines. In `src/phil/agents/registry.py`, delete `end_on_text=True` from the five lean specs. In `src/phil/agents/check.py`, delete `end_on_text=True` from `MODEL_CHECK` and replace the comment above it with: `# Lean, and without _shared.md: its self_check rules don't apply to this tiny contract. A text answer gets the same reminders a real agent gets, so a model passes only if Phil can get an answer from it.`

In `src/phil/store/telemetry.py`, add to `TelemetryRow` after `cache_read_tokens`: `reminders: int = 0  # reminders to call the answer tool (phil.agents.reminder)`. In `src/phil/store/db.py`, append to `MIGRATIONS`: `"ALTER TABLE telemetry ADD COLUMN reminders INTEGER NOT NULL DEFAULT 0",  # spec 2026-10-22 §4.2`.

In `src/phil/agents/invoke.py`, import `from phil.agents.reminder import reminder_count` and add `reminders=reminder_count(result.get("messages", [])),` to the `TelemetryRow(...)` built after each attempt.

LangGraph note: an `after_model` hook returning `structured_response` and `jump_to: "end"` sets the agent's structured response and ends the loop, with and without tools (probed against LangChain 1.4.3 while writing this plan).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/agents -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (other tests asserting `end_on_text` or one-call-per-attempt prose behaviour fail here; fix each the same way as `test_prose_answers.py`, and list them in the commit message).

- [ ] **Step 6: Commit**

```bash
git add src/phil/agents/reminder.py src/phil/agents/factory.py src/phil/agents/spec.py src/phil/agents/registry.py src/phil/agents/check.py src/phil/agents/invoke.py src/phil/store/telemetry.py src/phil/store/db.py tests/agents/test_reminder.py tests/agents/test_prose_answers.py tests/agents/test_factory.py
git commit -F .git/phil-commit-msg.txt   # "Agents: a text answer is taken or reminded, not dropped"
```

---

### Task 3: `phil models check` says when a model needed a reminder

**Files:**
- Modify: `src/phil/agents/check.py` (`CheckResult.reminded`, `_check_one`, `check_models`)
- Modify: `src/phil/cli/main.py:652` (the pass line)
- Test: `tests/agents/test_check.py`, `tests/cli/test_models_command.py`

**Interfaces:**
- Consumes: Task 2's `telemetry.reminders` column and reminder behaviour.
- Produces: `CheckResult(label, model, ok, seconds, detail, reminded: bool = False)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_check.py`:

```python
def test_a_pass_that_needed_a_reminder_says_so(tmp_path, monkeypatch):
    config = PhilConfig(models={"high": "ollama:small", "low": "ollama:small"})
    model = ScriptedChatModel(
        script=[AIMessage(content="ok pineapple"), tool_call("ModelCheck", {"ok": True, "echo": CHECK_WORD}, "c1")]
    )
    [result] = check_models(config, factory=real_factory(model, monkeypatch), repo_root=tmp_path)
    assert (result.ok, result.reminded) == (True, True)


def test_a_first_try_pass_needed_no_reminder(tmp_path, monkeypatch):
    config = PhilConfig(models={"high": "ollama:small", "low": "ollama:small"})
    model = ScriptedChatModel(script=[tool_call("ModelCheck", {"ok": True, "echo": CHECK_WORD}, "c1")])
    [result] = check_models(config, factory=real_factory(model, monkeypatch), repo_root=tmp_path)
    assert (result.ok, result.reminded) == (True, False)
```

Add to `tests/cli/test_models_command.py`:

```python
def test_models_check_marks_a_pass_that_needed_a_reminder(git_repo, monkeypatch):
    from phil.agents.check import CheckResult

    monkeypatch.setattr(cli, "console", make_console(width=300))
    monkeypatch.setattr(
        "phil.agents.check.check_models",
        lambda *a, **k: [CheckResult("high, low", "ollama:small", True, 2.5, "", reminded=True)],
    )
    (git_repo / "phil.toml").write_text('[models]\nhigh = "ollama:small"\nlow = "ollama:small"\n')
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "models", "check"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["✓ high, low  ollama:small  2.5s (needed a reminder)"]
```

(If `cli` imports `check_models` by name, monkeypatch `cli.check_models` instead; check the import at the top of `src/phil/cli/main.py`.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/agents/test_check.py tests/cli/test_models_command.py -q`
Expected: FAIL — `TypeError: CheckResult.__init__() got an unexpected keyword argument 'reminded'` and `AttributeError: 'CheckResult' object has no attribute 'reminded'`.

- [ ] **Step 3: Implement**

In `src/phil/agents/check.py`:
- `CheckResult` gains `reminded: bool = False  # passed, but only after a reminder to call the answer tool`.
- `_check_one` returns `tuple[bool, str, bool]`: every early `return False, <detail>` becomes `return False, <detail>, False`; the Jev pass `return True, "", False`; the final pass reads the check call's telemetry:

```python
    row = ctx.conn.execute(
        "SELECT reminders FROM telemetry WHERE node = ? AND call = ? ORDER BY id DESC LIMIT 1",
        ("model_check", call),
    ).fetchone()
    return True, "", bool(row and row[0])
```

  (keep the `ok=false` and wrong-echo failures before it, each returning `..., False`).
- `check_models`: `ok, detail, reminded = _check_one(...)` and `CheckResult(", ".join(labels), model, ok, clock() - started, detail, reminded)`.

In `src/phil/cli/main.py`, the pass line:

```python
        if result.ok:
            note = " (needed a reminder)" if result.reminded else ""
            line = f"[phil.gate.pass]✓[/] {escape(result.label)}  {escape(result.model)}  {result.seconds:.1f}s{note}"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/agents/test_check.py tests/cli/test_models_command.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/check.py src/phil/cli/main.py tests/agents/test_check.py tests/cli/test_models_command.py
git commit -F .git/phil-commit-msg.txt   # "models check: say when a model needed a reminder"
```

---

### Task 4: OpenAI's Responses API for real OpenAI

**Files:**
- Modify: `src/phil/agents/providers.py` (`openai` branch; new `_is_openai_api`)
- Test: `tests/agents/test_providers.py`, `tests/agents/test_collector.py`

**Interfaces:**
- Consumes: Task 1's `without_forced_tool_choice`.
- Produces: `_is_openai_api(base_url: str | None) -> bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_providers.py`:

```python
@pytest.mark.parametrize(
    ("base_url", "responses"),
    [
        (None, True),
        ("https://api.openai.com/v1", True),
        ("http://localhost:11434/v1", False),
        ("http://lab:8000/v1", False),
        ("https://api.groq.com/openai/v1", False),
    ],
)
def test_only_real_openai_uses_the_responses_api(base_url, responses):
    spec = ProviderSpec("x", "openai", base_url, None, None, None)
    assert build_chat_model(spec, "some-model", 30, environ={}).use_responses_api is responses
```

Add to `tests/agents/test_collector.py`:

```python
def test_a_responses_api_message_is_collected():
    # langchain_openai's Responses API returns content blocks and a resp_ id; usage still rides on
    # usage_metadata and the model on response_metadata["model_name"].
    collector = UsageCollector()
    message = AIMessage(
        content=[{"type": "text", "text": "hi", "annotations": [], "id": "msg_1"}],
        usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        response_metadata={"model_name": "gpt-5.6-terra", "id": "resp_1"},
    )
    collector.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=uuid.uuid4())
    [call] = collector.calls
    assert (call.model, call.input_tokens, call.output_tokens) == ("gpt-5.6-terra", 12, 3)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/agents/test_providers.py -k responses_api tests/agents/test_collector.py -k responses -q`
Expected: the provider cases FAIL (`use_responses_api` is `None`); the collector test may already PASS — it pins existing behaviour, so a pass here is expected and recorded, not a defect.

- [ ] **Step 3: Implement**

In `src/phil/agents/providers.py`, add `from urllib.parse import urlsplit` to the imports, and:

```python
def _is_openai_api(base_url: str | None) -> bool:
    """True for OpenAI itself (no base_url, or api.openai.com): it gets the Responses API, which
    reasoning models need for tools. OpenAI-compatible servers keep Chat Completions."""
    return base_url is None or urlsplit(base_url).hostname == "api.openai.com"
```

and the `openai` branch passes `use_responses_api=_is_openai_api(spec.base_url)` (an explicit `False` for compatible servers, so LangChain never switches them to an API they don't serve).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/agents/test_providers.py tests/agents/test_tool_choice.py tests/agents/test_collector.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/agents/providers.py tests/agents/test_providers.py tests/agents/test_collector.py
git commit -F .git/phil-commit-msg.txt   # "OpenAI: the Responses API for OpenAI itself"
```

---

### Task 5: Error messages

**Files:**
- Modify: `src/phil/agents/retry.py` (quota phrases move here; `looks_like_quota`; `local_server_down`; `is_transient`)
- Modify: `src/phil/agents/failures.py` (provider tag, key hint, Google bad key, local server, quota first; drop `_KEY_ENV`)
- Modify: `src/phil/agents/invoke.py` (tag the exception with the provider before re-raising)
- Test: `tests/agents/test_failures.py`, `tests/agents/test_retry.py`

**Interfaces:**
- Produces: `retry.looks_like_quota(text: str) -> bool`, `retry.QUOTA_PHRASES`, `retry.local_server_down(exc: BaseException) -> str | None` (`"scheme://host:port"`), `failures.PROVIDER_ATTR = "phil_provider"`, `failures.tag_provider(exc: BaseException, spec: ProviderSpec) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_retry.py` (it already imports `httpx`, `openai` or add them):

```python
import httpx
import openai

from phil.agents.retry import is_transient, local_server_down


def _connection_error(url: str) -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", url))


def test_a_refused_local_server_is_found_and_not_retried():
    exc = _connection_error("http://localhost:11434/v1/chat/completions")
    assert local_server_down(exc) == "http://localhost:11434"
    assert is_transient(exc) is False


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/v1/x", "http://[::1]:8000/v1/x"])
def test_every_loopback_host_counts(url):
    assert local_server_down(_connection_error(url)) is not None


def test_a_remote_connection_error_is_still_retried():
    exc = _connection_error("https://api.openai.com/v1/responses")
    assert local_server_down(exc) is None
    assert is_transient(exc) is True


def test_a_local_timeout_is_still_a_retried_timeout():
    exc = openai.APITimeoutError(request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"))
    assert local_server_down(exc) is None
    assert is_transient(exc) is True


def test_a_quota_429_is_not_retried():
    exc = openai.RateLimitError(
        "You exceeded your current quota, please check your plan.",
        response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/x")),
        body=None,
    )
    assert is_transient(exc) is False
```

Add to `tests/agents/test_failures.py`:

```python
from phil.agents.failures import tag_provider
from phil.agents.providers import BUILTIN_PROVIDERS, ProviderSpec


def test_a_custom_providers_auth_failure_names_its_own_key_variable():
    exc = HTTPError(401)
    tag_provider(exc, ProviderSpec("vllm", "openai", "http://box:8000/v1", "VLLM_API_KEY", None, None))
    f = classify_failure(exc)  # the run worker passes no provider
    assert f.category == "auth"
    assert f.action == "Run `phil keys set vllm`, or set VLLM_API_KEY."


def test_an_overridden_key_variable_is_named():
    exc = HTTPError(401)
    tag_provider(exc, ProviderSpec("openai", "openai", None, "WORK_OPENAI_KEY", None, None))
    assert classify_failure(exc, provider="openai").action == "Run `phil keys set openai`, or set WORK_OPENAI_KEY."


def test_a_keyless_provider_gets_no_keys_set_hint():
    exc = HTTPError(401)
    tag_provider(exc, BUILTIN_PROVIDERS["ollama"])
    f = classify_failure(exc)
    assert "phil keys set" not in f.action
    assert f.action == "Check [providers.ollama] in your config: it sends no API key."


def test_an_untagged_builtin_still_gets_its_default_key():
    assert classify_failure(HTTPError(401), provider="anthropic").action == (
        "Run `phil keys set anthropic`, or set ANTHROPIC_API_KEY."
    )


def test_an_untagged_unknown_provider_points_at_keys_list():
    assert classify_failure(HTTPError(401), provider="mystery").action == "Check your API keys with `phil keys list`."


def test_googles_bad_key_400_is_an_auth_failure():
    exc = GoogleInvalidRequestError(
        "Error calling model 'gemini-3.8-flash' (INVALID_ARGUMENT): 400 INVALID_ARGUMENT. "
        "{'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.', "
        "'status': 'INVALID_ARGUMENT', 'details': [{'reason': 'API_KEY_INVALID'}]}}"
    )
    f = classify_failure(exc, provider="google")
    assert f.category == "auth"
    assert f.action == "Run `phil keys set google`, or set GOOGLE_API_KEY."


def test_a_local_server_that_is_down_says_so():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"))
    tag_provider(exc, BUILTIN_PROVIDERS["ollama"])
    f = classify_failure(exc)
    assert f.category == "network"
    assert f.headline == "Couldn't reach ollama at http://localhost:11434/v1."
    assert f.retries == "Phil won't retry this."
    assert f.action == "Is `ollama serve` running?"


def test_another_local_server_that_is_down_names_its_url():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:8000/v1/chat/completions"))
    tag_provider(exc, ProviderSpec("vllm", "openai", "http://localhost:8000/v1", None, None, None))
    f = classify_failure(exc)
    assert f.headline == "Couldn't reach vllm at http://localhost:8000/v1."
    assert f.action == "Is the server at http://localhost:8000/v1 running?"


def test_an_untagged_local_server_uses_the_request_url():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:9000/v1/x"))
    f = classify_failure(exc, provider="lab")
    assert f.headline == "Couldn't reach lab at http://127.0.0.1:9000."


def test_googles_quota_error_is_quota_not_busy():
    f = classify_failure(GoogleRateLimitError("You exceeded your current quota."), provider="google")
    assert f.category == "quota"
```

If `test_failures.py` has no `GoogleInvalidRequestError` stand-in next to its `GoogleAuthenticationError` / `GoogleRateLimitError` ones, add one the same way, subclassing langchain_core's `ModelInvalidRequestError` (look at how the existing two are defined at the top of the file and mirror them).

Add to the invoke tests (`tests/agents/test_invoke.py`, next to an existing test that makes a model raise):

```python
def test_a_failing_call_is_tagged_with_its_provider(config, conn, tmp_path, monkeypatch):
    from phil.agents.failures import PROVIDER_ATTR
    from tests.agents.test_model_retry import HTTPError, ScriptedChatModel, real_factory

    model = ScriptedChatModel(script=[HTTPError(401)])
    ctx = AgentContext(config=config, conn=conn, layer="chat", factory=real_factory(model, monkeypatch))
    with pytest.raises(Exception) as excinfo:
        invoke_agent(get_spec("critic"), _critic_packet(), ctx, node="critic")
    assert getattr(excinfo.value, PROVIDER_ATTR).name == config.model_for("critic").partition(":")[0]
```

(Reuse the critic packet helper the file already has, or the one from `tests/agents/test_reminder.py`.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/agents/test_retry.py tests/agents/test_failures.py tests/agents/test_invoke.py -q`
Expected: FAIL — `ImportError: cannot import name 'local_server_down'` / `'tag_provider'`.

- [ ] **Step 3: Implement**

In `src/phil/agents/retry.py`, after `add_provider_detail`:

```python
# Specific phrases, not single keywords: a bare "credit", "quota", "billing" or "payment" also
# shows up in refusals that have nothing to do with the account being out of money (e.g. "model
# billing-assistant not found", "payment field invalid"), so those would false-positive as quota.
QUOTA_PHRASES = (
    "insufficient credits",
    "insufficient_quota",
    "insufficient funds",
    "out of credits",
    "exceeded your current quota",
    "credit balance",
    "requires more credits",
)


def looks_like_quota(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in QUOTA_PHRASES)


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_CONNECT_NAMES = {"APIConnectionError", "ConnectError"}


def _request_url(exc: BaseException) -> Any:
    try:
        url = exc.request.url  # type: ignore[attr-defined]  # openai/anthropic/httpx errors carry the request
    except Exception:
        return None
    return url if getattr(url, "host", None) is not None else None


def local_server_down(exc: BaseException) -> str | None:
    """`scheme://host:port` of a local server (localhost, 127.0.0.1, ::1) that refused the
    connection, from `exc` or its causes; None otherwise. A timeout is not this: a slow server is
    running."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names = {cls.__name__ for cls in type(current).__mro__}
        if (isinstance(current, ConnectionRefusedError) or names & _CONNECT_NAMES) and not is_timeout(current):
            url = _request_url(current)
            if url is not None and url.host in _LOOPBACK:
                return f"{url.scheme}://{url.netloc.decode('ascii')}"
        current = current.__cause__ or current.__context__
    return None
```

and at the top of `is_transient`:

```python
def is_transient(exc: BaseException) -> bool:
    # A local server that isn't running won't start by itself, and an exhausted quota won't refill
    # in seconds: neither is worth a retry.
    if local_server_down(exc) is not None or looks_like_quota(provider_detail(exc) or str(exc)):
        return False
```

(`is_timeout` is defined above `is_transient`; `local_server_down` calls it at run time, so its position in the file doesn't matter.)

In `src/phil/agents/failures.py`:
- delete `_KEY_ENV`, `_QUOTA_PHRASES` (and its comment, which moves to retry.py) and `_looks_like_quota`.
- add:

```python
PROVIDER_ATTR = "phil_provider"  # the ProviderSpec invoke_agent tags a failed call's exception with
_BAD_KEY_PHRASES = ("api_key_invalid", "api key not valid")


def tag_provider(exc: BaseException, spec: "ProviderSpec") -> None:
    """Remember which provider `exc` came from, so its failure names the right key and URL."""
    try:
        setattr(exc, PROVIDER_ATTR, spec)
    except Exception:
        pass


def _key_action(provider: str | None, spec: "ProviderSpec | None") -> str:
    if provider is None:
        return "Check your API keys with `phil keys list`."
    if spec is None:
        from phil.agents.providers import ALIASES, BUILTIN_PROVIDERS

        spec = BUILTIN_PROVIDERS.get(ALIASES.get(provider, provider))
        if spec is None:
            return "Check your API keys with `phil keys list`."
    if spec.api_key_env is None:
        return f"Check [providers.{provider}] in your config: it sends no API key."
    return f"Run `phil keys set {provider}`, or set {spec.api_key_env}."
```

  with `from typing import TYPE_CHECKING` and `if TYPE_CHECKING: from phil.agents.providers import ProviderSpec` at the top.
- `_auth_failure`:

```python
def _auth_failure(provider: str | None, spec: "ProviderSpec | None" = None) -> Failure:
    return Failure("auth", "The provider rejected your API key.", "Phil won't retry this.", _key_action(provider, spec))
```
- `_classify` becomes (the whole function):

```python
def _classify(
    exc: BaseException, provider: str | None, attempts: int | None, spec: "ProviderSpec | None"
) -> Failure:
    from phil.agents.invoke import ContractViolation
    from phil.agents.retry import is_transient, local_server_down, looks_like_quota, provider_detail
    from phil.config import ConfigError

    if isinstance(exc, ContractViolation):
        return Failure("output", f"The {exc.agent} didn't return a usable answer.", "Phil already retried.",
                       f"Try again, or use a stronger model for {exc.agent}.")
    if isinstance(exc, ConfigError):
        return Failure("config", str(exc), "Phil won't retry this.", "Fix the setting it names.")
    if (unsendable := _unsendable_content(exc, provider)) is not None:
        return unsendable
    status = _status(exc)
    detail = provider_detail(exc) or str(exc)
    if any(phrase in detail.lower() for phrase in _BAD_KEY_PHRASES):  # Google's bad key is a 400
        return _auth_failure(provider, spec)
    client_error = status is None or 400 <= status < 500
    if status == 402 or (client_error and looks_like_quota(detail)):  # before "busy": a 429 can be quota
        return Failure("quota", f"Your {provider or 'provider'} account is out of credits.", "Phil won't retry this.",
                       "Add credits, then try again.")
    if (local := local_server_down(exc)) is not None:
        base = spec.base_url if spec is not None and spec.base_url else local
        action = "Is `ollama serve` running?" if provider == "ollama" else f"Is the server at {base} running?"
        return Failure("network", f"Couldn't reach {_object(provider)} at {base}.", "Phil won't retry this.", action)
    if status is None:
        names = _mro_names(exc)
        if names & _AUTH_NAMES:
            return _auth_failure(provider, spec)
        if names & _BUSY_NAMES:
            return _busy_failure(provider, attempts)
        if names & _REFUSED_NAMES:
            return _refused_failure(provider, detail)
    if status in (401, 403):
        return _auth_failure(provider, spec)
    if status in (429, 529):
        return _busy_failure(provider, attempts)
    if status is not None and 500 <= status < 600:
        return Failure("server", f"{_subject(provider)} had a server error. Phil retried.", "Phil already retried.",
                       "Try again in a few minutes.")
    if is_transient(exc):
        return Failure("network", f"Couldn't reach {_object(provider)}. Phil retried.", "Phil already retried.",
                       "Check your connection, then try again.")
    if status is not None and 400 <= status < 500:
        return _refused_failure(provider, detail)
    return internal_failure(type(exc).__name__)
```

  and `classify_failure`:

```python
def classify_failure(exc: BaseException, *, provider: str | None = None, attempts: int | None = None) -> Failure:
    try:
        spec = getattr(exc, PROVIDER_ATTR, None)
        name = provider or (spec.name if spec is not None else None)
        return _classify(exc, name, attempts, spec)
    except Exception:
        return internal_failure(type(exc).__name__)
```

In `src/phil/agents/invoke.py`, in the `except Exception as exc:` block of the attempt loop, just before the bare `raise` (after `_record_error`), add:

```python
                if provider is not None:
                    tag_provider(exc, provider)
```

with `from phil.agents.failures import tag_provider` at the top (failures.py imports invoke only inside a function, so there is no cycle).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/agents/test_retry.py tests/agents/test_failures.py tests/agents/test_invoke.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. Tests elsewhere that asserted the old `_KEY_ENV` fallback action text for an unknown provider (`Run \`phil keys set x\`, or set X_API_KEY.`) now get `Check your API keys with \`phil keys list\`.`; update them and list them in the commit message.

- [ ] **Step 6: Commit**

```bash
git add src/phil/agents/retry.py src/phil/agents/failures.py src/phil/agents/invoke.py tests/agents/test_retry.py tests/agents/test_failures.py tests/agents/test_invoke.py
git commit -F .git/phil-commit-msg.txt   # "Failures: the right key, a down local server, Google's bad key, quota first"
```

---

### Task 6: Setup suggestions and Ollama models that can't call tools

**Files:**
- Modify: `src/phil/setup/suggestions.py`
- Modify: `src/phil/setup/catalog.py` (`OllamaModel`, `ollama_models`)
- Modify: `src/phil/setup/flow.py` (`_Provider.installed`, `_ollama_provider`, `_ollama_model`, the `ollama` callable type)
- Test: `tests/setup/test_catalog.py`, `tests/setup/test_flow.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class OllamaModel: name: str; tools: bool | None` (None: this Ollama doesn't report capabilities); `ollama_models(base_url, *, get=httpx.get, timeout=3.0) -> list[OllamaModel] | None`; `OLLAMA_NO_TOOLS = "  — can't call tools, won't work with Phil"` in flow.py.

- [ ] **Step 1: Write the failing tests**

In `tests/setup/test_catalog.py`, change `test_ollama_models_lists_the_installed_models` to feed `capabilities` and expect `OllamaModel`s:

```python
def test_ollama_models_lists_the_installed_models_and_whether_they_call_tools():
    def get(url, timeout):
        return httpx.Response(
            200,
            json={"models": [
                {"name": "qwen3:32b", "capabilities": ["completion", "tools", "thinking"]},
                {"name": "qwen2.5-coder:1.5b", "capabilities": ["completion"]},
                {"name": "llama4:8b"},
            ]},
            request=httpx.Request("GET", url),
        )

    assert ollama_models("http://localhost:11434/v1", get=get) == [
        OllamaModel("qwen3:32b", True),
        OllamaModel("qwen2.5-coder:1.5b", False),
        OllamaModel("llama4:8b", None),
    ]
```

(Keep the existing tests' `get` shape; read the current test to match how it builds the response, and update `test_ollama_models_handles_a_base_url_without_v1` to expect `[]` as before.)

In `tests/setup/test_flow.py`, make `FakeOllama` accept plain names:

```python
class FakeOllama:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def __call__(self, base_url):
        self.urls.append(base_url)
        answer = self.answers.pop(0)
        if answer is None:
            return None
        return [m if isinstance(m, OllamaModel) else OllamaModel(m, None) for m in answer]
```

(import `OllamaModel` from `phil.setup.catalog`) and add:

```python
def test_an_ollama_model_that_cant_call_tools_is_marked_and_not_the_default(tmp_path):
    ollama = FakeOllama([OllamaModel("qwen2.5-coder:1.5b", False), OllamaModel("qwen3:8b", True)])
    wrote, io, _ = setup(["Ollama", "", "", "Your low model"], tmp_path, ollama=ollama)
    assert wrote is True
    assert any("qwen2.5-coder:1.5b  — can't call tools, won't work with Phil" in line for line in io.lines)
    assert written(tmp_path).models == {"high": "ollama:qwen3:8b", "low": "ollama:qwen3:8b"}


def test_the_new_suggestions():
    from phil.setup.suggestions import SUGGESTIONS

    assert SUGGESTIONS["anthropic"] == {"high": "anthropic:claude-opus-5-5", "low": "anthropic:claude-haiku-5-5"}
    assert SUGGESTIONS["google"] == {"high": "google:gemini-4-argon", "low": "google:gemini-3.8-flash"}
    assert SUGGESTIONS["openai"] == {"high": "openai:gpt-5.6-sol", "low": "openai:gpt-5.6-terra"}
```

(The answer script follows `test_unreachable_ollama_then_retry`'s shape — Enter takes each default. If the final "Your low model" step differs, copy the closing answers from `test_the_ollama_path_picks_installed_models`.) Tests that pick Google and type both model ids now see suggested defaults: update their answers to accept or override the defaults, and list them in the commit message.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/setup -q`
Expected: FAIL — `ImportError: cannot import name 'OllamaModel'`.

- [ ] **Step 3: Implement**

`src/phil/setup/suggestions.py` — update the docstring's dated list (Anthropic: "the Claude 5.5 family, `claude-opus-5-5` (flagship) and `claude-haiku-5-5` (fastest); confirmed live 2026-10-22, with Phil no longer forcing tool calls"; Google: "`gemini-4-argon` and `gemini-3.8-flash`, confirmed live 2026-10-22"; drop "Google is left out…"), and set:

```python
    "anthropic": {
        "high": "anthropic:claude-opus-5-5",
        "low": "anthropic:claude-haiku-5-5",
    },
    "google": {
        "high": "google:gemini-4-argon",
        "low": "google:gemini-3.8-flash",
    },
```

`src/phil/setup/catalog.py`:

```python
@dataclass(frozen=True)
class OllamaModel:
    name: str
    tools: bool | None  # can it call tools; None when this Ollama doesn't report capabilities


def ollama_models(
    base_url: str, *, get: Callable[..., Any] = httpx.get, timeout: float = 3.0
) -> list[OllamaModel] | None:
    """The models installed in the Ollama at `base_url` (its OpenAI-compatible `/v1` URL or its
    root), from `GET /api/tags`, with whether each can call tools. None when it can't be reached or
    answers unexpectedly."""
    root = base_url.rstrip("/").removesuffix("/v1").rstrip("/")
    try:
        response = get(f"{root}/api/tags", timeout=timeout)
        response.raise_for_status()
        models = []
        for model in response.json()["models"]:
            capabilities = model.get("capabilities")
            tools = None if capabilities is None else "tools" in capabilities
            models.append(OllamaModel(str(model["name"]), tools))
        return models
    except Exception:
        return None
```

`src/phil/setup/flow.py`:
- import `OllamaModel`; the `ollama` parameter types become `Callable[[str], list[OllamaModel] | None]` (in `run_setup`'s signature and `_provider_step` / `_ollama_provider`).
- `_Provider.installed: list[OllamaModel] = field(default_factory=list)  # Ollama's models`.
- add `OLLAMA_NO_TOOLS = "  — can't call tools, won't work with Phil"` near `PROVIDER_CHOICES`.
- `_ollama_model`:

```python
def _ollama_model(io: SetupIO, provider: _Provider, tier: str, default: str | None) -> str:
    models = provider.installed
    names = [m.name for m in models]
    labels = [m.name + (OLLAMA_NO_TOOLS if m.tools is False else "") for m in models]
    current = default.removeprefix("ollama:") if default else None
    if current in names:
        start = names.index(current)
    else:
        start = next((i for i, m in enumerate(models) if m.tools is not False), 0)
    index = io.choose(f"{tier} model", labels, default=start)
    return f"ollama:{names[index]}"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/setup tests/cli/test_setup_command.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phil/setup/suggestions.py src/phil/setup/catalog.py src/phil/setup/flow.py tests/setup/test_catalog.py tests/setup/test_flow.py
git commit -F .git/phil-commit-msg.txt   # "Setup: current Claude and Gemini suggestions; mark Ollama models that can't call tools"
```

---

### Task 7: Docs and live acceptance

**Files:**
- Modify: `docs/guide.md` (Providers, Checking your models)
- Modify: `docs/superpowers/roadmap.md` (release item)

- [ ] **Step 1: Docs**

In `docs/guide.md`, under `### Providers`, after the paragraph that starts "`kind` picks the SDK", add:

```markdown
Phil never forces a model to call a tool: current Claude models reject that. An agent that
answers in plain text instead gets its JSON taken as the answer when it's valid, or a short
reminder to call its answer tool (up to two per attempt). The `openai` provider (OpenAI
itself) uses OpenAI's Responses API, which its reasoning models need for tools; every other
`openai`-kind provider (Ollama, vLLM, a custom endpoint) uses Chat Completions.

A local model has to support tool calling to work with Phil. `phil setup` marks Ollama models
that can't ("can't call tools, won't work with Phil").
```

Under `### Checking your models`, append: ``A pass marked `(needed a reminder)` means the model first answered in text and returned the structured output only after a reminder: it works, but it's a weak fit and costs an extra call now and then.``

In `docs/superpowers/roadmap.md`, the release item "confirm the bring-your-own-provider path (M2a)" becomes: `confirm the bring-your-own-provider path (M2a): answers on every provider ([spec](specs/2026-10-22-phil-provider-answers-design.md), [plan](plans/2026-10-22-phil-provider-answers.md)); prices and budgets next;`.

- [ ] **Step 2: Full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add docs/guide.md docs/superpowers/roadmap.md
git commit -F .git/phil-commit-msg.txt   # "Docs: answers on every provider"
```

- [ ] **Step 4: Live acceptance (Claude runs; user's stored keys; throwaway PHIL_HOME)**

Never `pytest -m live` / `-m bench`. In the session scratchpad: `mkdir -p <scratch>/home` and write `<scratch>/home/config.toml` with `[models]` `high`/`low` set to `ollama:qwen3:8b`. Then for each pair run `PHIL_HOME=<scratch>/home uv run phil --set models.high=<a> --set models.low=<b> models check`:

| high | low |
|---|---|
| `openai:gpt-5.6-sol` | `openai:gpt-5.6-terra` |
| `anthropic:claude-opus-5-5` | `anthropic:claude-haiku-5-5` |
| `anthropic:claude-sonnet-5-5` | `anthropic:claude-opus-5` |
| `google:gemini-4-argon` | `google:gemini-3.8-flash` |
| `openrouter:anthropic/claude-sonnet-5-5` | `openrouter:anthropic/claude-sonnet-5` |
| `ollama:qwen3:8b` | `ollama:gemma4:12b` |

Expected: every line `✓` (Ollama may show `(needed a reminder)`).

Also `PHIL_HOME=<scratch>/home uv run phil --set providers.lab.kind=openai --set providers.lab.base_url=http://localhost:11999/v1 --set models.high=lab:x --set models.low=lab:x models check` → the failure line names the down server (models check prints the raw error detail; confirm `classify_failure` on it by eye: "Couldn't reach lab at http://localhost:11999/v1." / "Is the server at http://localhost:11999/v1 running?"). If `--set` can't create a provider table, write `[providers.lab]` into the scratch config instead.

Then one small quick-path run per provider in a scratch git repo (`git init`, one file `greet.py` with `def greet(): return "hi"`, a `phil.toml` with `[project] test_cmd = "python -c 'import greet; assert greet.greet() == \"hello\"'"`), goal: "Change greet() to return hello". For each of: Anthropic (`claude-opus-5-5`/`claude-haiku-5-5`), OpenAI (`gpt-5.6-sol`/`gpt-5.6-terra`), Google (`gemini-4-argon`/`gemini-3.8-flash`), Ollama (`qwen3:8b` for both) — start it the way a user would from the chat (`/quick Change greet() to return hello`), or with `phil run` and a one-task plan if driving the chat isn't practical, and confirm the run completes with the change made. Record each provider's result, model calls, reminders (`SELECT SUM(reminders) FROM telemetry` in the run's DB) and cost line in the final report. Then ask the user to run the OpenRouter benchmark.
