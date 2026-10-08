# Decision and Failure Callouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every choice prompt in the chat becomes a bordered callout, docked above the input and answered with ↑/↓ and Enter (or a number). Failures become plain-language callouts that say whether Phil retries and what to do.

**Architecture:**
- **Pure pieces:**
  - a `Decision` model, plus builders for every stage and pause reason (`phil/chat/decision.py`);
  - a failure classifier (`phil/agents/failures.py`);
  - one callout renderer with two outputs, prompt_toolkit fragments and Rich (`phil/ui/callout.py`).
- **The terminal** gets a decision mode on its existing prompt, through a new optional `ChatIO.choose`.
- **The controller** builds a `Decision` per stage. It feeds the picked option's `answer` into its existing input handlers, so their logic is unchanged.

**Tech Stack:** Python 3.14, prompt_toolkit (key bindings, formatted text), Rich, pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-phil-callouts-design.md`

## Global Constraints

- **Editing and committing**
  - Edit files only with the Edit or Write tools. Never edit through python, perl, sed, heredocs or printf in Bash.
  - To commit, write the message to a file with Write, then run `git commit -F <file>`.
  - Every message ends with a blank line, then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, whatever model you are.
- **Safety**
  - Keep the words "keychain" and "credentials" out of Bash command lines.
  - Never work around a hook or guard; if one blocks you, report BLOCKED.
  - Never read or print `.env` files or key values.
- **Tests**
  - Never run `-m live` or `-m bench`.
  - Iterate with `uv run pytest <paths> -q -n 0`. Run the full `uv run pytest -q` once at the end of each task.
  - CI runs Ubuntu, macOS and Windows: keep tests portable.
- **Text I/O** uses `encoding="utf-8"`, and writes use `newline="\n"`.
- **No markup injection.** All plan, agent and error text is rendered as `Text` or plain fragments, never as Rich markup.
- **Copy:** the labels, titles and headlines in this plan are verbatim from the spec. When the brief gives exact expected strings, those strings are the contract.
- **Constants:**

| Name | Value |
|---|---|
| `BODY_LINES` | 8 |
| `NARROW` | 40 (columns: below this, no border) |
| `KEY_HINT` | `↑/↓ choose · Enter confirm · 1–{n} · Esc type a message` |

## Review Focus

1. **A pause callout is open and the run is answered from another terminal.** The callout closes with `{run} was answered elsewhere.`, and the next prompt is a normal one. Tested in Task 5.
2. **A wake arrives while the menu is open** (feed lines arriving). The menu comes back with the same option highlighted. Tested in Task 4.
3. **Pressing Enter by reflex on a pause never aborts the run.** The default is never `abort`. Tested in Task 1.
4. **Piped input (`LineIO`)** still answers with typed numbers and words, so existing tests and CI pipes behave as before. Tested in Task 4.
5. **An exception with no status and no known class** classifies as `internal`, and never raises from the classifier. Tested in Task 2.

---

### Task 1: The `Decision` model and builders

**Files:**
- Create: `src/phil/chat/decision.py`, `tests/chat/test_decision.py`

**Interfaces:**
- Produces:
  - **Dataclasses:**
    - `Option(label: str, answer: str, typed: bool = False, detail: str = "")`, frozen.
    - `Decision(kind: str, title: str, body: tuple[str, ...], options: tuple[Option, ...], default: int = 0, settled_prefix: str = "✓")`, frozen.
  - **Builders:**

| Function | Signature |
|---|---|
| `settled_line` | `(decision, option) -> str` |
| `pause_decision` | `(run_id: str, escalation: dict) -> Decision` |
| `question_decision` | `(index: int, total: int, text: str, why: str, options: list[str]) -> Decision \| None` |
| `approach_decision` | `(approaches: list[tuple[str, str]]) -> Decision` |
| `approval_decision` | `() -> Decision` |
| `pr_decision` | `(run_id: str, force: bool) -> Decision` |
| `fix_decision` | `() -> Decision` |
| `replace_decision` | `() -> Decision` |

  - **Constants:** `BODY_LINES = 8`, `LABELS: dict[str, str]`, `DEFAULT_FOR: dict[str, str]`.
  - `answered_elsewhere(run_id) -> str`, which returns `f"{run_id} was answered elsewhere."`

- [ ] **Step 1: Write the failing tests**

```python
# tests/chat/test_decision.py
import pytest

from phil.chat.decision import (
    BODY_LINES, approach_decision, approval_decision, fix_decision, pause_decision, pr_decision,
    question_decision, replace_decision, settled_line,
)


def labels(decision):
    return [o.label for o in decision.options]


def answers(decision):
    return [o.answer for o in decision.options]


def test_approval_pause():
    d = pause_decision("r-4f2a", {"reason": "approval", "task_id": "CALC-002", "commands": ["node build.mjs"],
                                  "options": ["approve", "deny", "abort"], "summary": "x"})
    assert d.kind == "approval"
    assert d.title == "⏸ r-4f2a needs you · approve a command"
    assert d.body == ("CALC-002 wants to run:", "  node build.mjs",
                      "Approving allows these exact commands for the rest of this run only.")
    assert labels(d) == ["Approve for this run", "Deny: the agent continues without it", "Abort the run"]
    assert answers(d) == ["approve", "deny", "abort"]
    assert d.default == 0
    assert settled_line(d, d.options[0]) == "✓ Approve for this run · node build.mjs"


def test_attempts_pause_lists_three_problems_and_defaults_to_retry():
    d = pause_decision("r-1", {"reason": "attempts", "task_id": "CALC-001", "phase": "green",
                               "problems": ["a", "b", "c", "d"], "options": ["retry", "skip", "full", "abort"],
                               "summary": "CALC-001 failed 3 attempts in the green phase"})
    assert d.kind == "pause"
    assert d.title == "⏸ r-1 needs you · CALC-001 failed 3 attempts (green phase)"
    assert d.body == ("a", "b", "c", "Details: /more")
    assert labels(d) == ["Retry the task", "Skip this task", "Plan it fully instead", "Abort the run"]
    assert d.default == 0


@pytest.mark.parametrize("reason,options,title_end,default_answer", [
    ("cmd_not_found", ["retry", "abort"], "a command isn't installed", "retry"),
    ("setup_failed", ["retry", "abort"], "setup failed", "retry"),
    ("no_test_cmd", ["retry", "skip", "abort"], "no test command", "retry"),
    ("commit_failed", ["retry", "bypass", "abort"], "the commit failed", "retry"),
    ("review_failed", ["retry", "finish", "abort"], "the reviewer gave no valid review", "retry"),
    ("budget", ["continue", "abort"], "the budget is used up", "continue"),
])
def test_every_pause_reason_has_a_title_and_a_safe_default(reason, options, title_end, default_answer):
    d = pause_decision("r-1", {"reason": reason, "options": options, "summary": "s", "problems": ["p"]})
    assert d.title == f"⏸ r-1 needs you · {title_end}"
    assert d.options[d.default].answer == default_answer
    assert d.options[-1].answer == "abort"


def test_abort_is_never_the_default_even_alone_first():
    d = pause_decision("r-1", {"reason": "mystery", "options": ["abort", "retry"], "summary": "Something odd"})
    assert d.options[d.default].answer != "abort"
    assert d.options[-1].answer == "abort"
    assert d.title == "⏸ r-1 needs you · Something odd"


def test_a_long_body_is_capped():
    d = pause_decision("r-1", {"reason": "review_failed", "options": ["retry", "abort"], "summary": "s",
                               "problems": [f"p{i}" for i in range(20)]})
    assert len(d.body) <= BODY_LINES
    assert d.body[-1] == "… see /more 1"


def test_question_decision():
    d = question_decision(2, 3, "Which database?", "It changes the schema.", ["Postgres", "SQLite"])
    assert d.kind == "question"
    assert d.title == "Question 2 of 3: Which database?"
    assert d.body == ("It changes the schema.",)
    assert labels(d) == ["Postgres", "SQLite", "Something else (type it)", "Plan with what you know"]
    assert answers(d) == ["1", "2", "3", "go"]
    assert d.options[2].typed
    assert settled_line(d, d.options[0]) == "Answer: Postgres"


def test_a_question_without_options_is_free_text():
    assert question_decision(1, 1, "Anything else?", "", []) is None


def test_approach_decision():
    d = approach_decision([("Inline", "Add it to calc.py."), ("Module", "A new module.")])
    assert labels(d) == ["Inline (recommended)", "Module", "Describe your own"]
    assert answers(d) == ["1", "2", "3"]
    assert d.options[0].detail == "Add it to calc.py." and d.options[2].typed


def test_plan_approval_and_confirmations():
    assert answers(approval_decision()) == ["y", "edit", "n"]
    assert labels(approval_decision()) == ["Approve and run", "Edit the plan", "Cancel"]
    assert approval_decision().options[1].typed
    assert labels(pr_decision("r-1", force=False)) == ["Open the PR", "Not now"]
    assert labels(pr_decision("r-1", force=True)) == ["Open the PR anyway", "Not now"]
    assert answers(pr_decision("r-1", force=False)) == ["y", "n"]
    assert labels(fix_decision()) == ["Quick fix", "Plan it fully", "Not now"]
    assert answers(fix_decision()) == ["y", "full", "n"]
    assert labels(replace_decision()) == ["Replace it", "Keep the current goal"]
    assert answers(replace_decision()) == ["y", "n"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/chat/test_decision.py -q -n 0`
Expected: FAIL, because the module doesn't exist.

- [ ] **Step 3: Implement**

```python
# src/phil/chat/decision.py
"""What the chat asks, as data (spec 2026-10-07 callouts §3.1): a Decision is rendered as a
callout and answered from a menu. Each option's `answer` is the text the chat's existing input
handlers already accept, so picking from the menu and typing are the same thing to the chat."""

from dataclasses import dataclass

BODY_LINES = 8

LABELS = {
    "retry": "Retry the task",
    "skip": "Skip this task",
    "full": "Plan it fully instead",
    "approve": "Approve for this run",
    "deny": "Deny: the agent continues without it",
    "continue": "Keep going past the budget",
    "bypass": "Commit without signing or hooks",
    "finish": "Finish without a review",
    "abort": "Abort the run",
}
DEFAULT_FOR = {
    "attempts": "retry", "cmd_not_found": "retry", "setup_failed": "retry", "commit_failed": "retry",
    "review_failed": "retry", "no_test_cmd": "retry", "approval": "approve", "budget": "continue",
}
_TITLES = {
    "approval": "approve a command",
    "cmd_not_found": "a command isn't installed",
    "setup_failed": "setup failed",
    "no_test_cmd": "no test command",
    "commit_failed": "the commit failed",
    "review_failed": "the reviewer gave no valid review",
    "budget": "the budget is used up",
}


@dataclass(frozen=True)
class Option:
    label: str
    answer: str
    typed: bool = False
    detail: str = ""


@dataclass(frozen=True)
class Decision:
    kind: str  # question | approval | pause | confirm
    title: str
    body: tuple[str, ...]
    options: tuple[Option, ...]
    default: int = 0
    settled_prefix: str = "✓"
    note: str = ""  # appended to the settled line (an approval's commands)


def _capped(lines: list[str]) -> tuple[str, ...]:
    if len(lines) <= BODY_LINES:
        return tuple(lines)
    return (*lines[: BODY_LINES - 1], "… see /more 1")


def settled_line(decision: Decision, option: Option) -> str:
    if decision.kind == "question":
        return f"Answer: {option.label}"
    prefix = "✗" if option.answer == "abort" else decision.settled_prefix
    text = f"{prefix} {option.label}"
    return f"{text} · {decision.note}" if decision.note else text


def answered_elsewhere(run_id: str) -> str:
    return f"{run_id} was answered elsewhere."


def pause_decision(run_id: str, escalation: dict) -> Decision:
    reason = str(escalation.get("reason", ""))
    words = [str(w) for w in escalation.get("options") or ["abort"]]
    words = [w for w in words if w != "abort"] + ["abort"]  # abort always last
    options = tuple(Option(LABELS.get(w, w.capitalize()), w) for w in words)
    wanted = DEFAULT_FOR.get(reason)
    default = next((i for i, o in enumerate(options) if o.answer == wanted), 0)
    if options[default].answer == "abort":
        default = 0 if options[0].answer != "abort" else default
    problems = [str(p) for p in escalation.get("problems") or []]
    summary = str(escalation.get("summary", ""))
    note = ""
    if reason == "attempts":
        task, phase = escalation.get("task_id", ""), escalation.get("phase", "")
        count = summary.split(" failed ")[1].split(" ")[0] if " failed " in summary else "several"
        title_end = f"{task} failed {count} attempts ({phase} phase)"
        body = [*problems[:3], "Details: /more"]
    elif reason == "approval":
        commands = [str(c) for c in escalation.get("commands") or []]
        title_end = _TITLES[reason]
        body = [f"{escalation.get('task_id', 'The task')} wants to run:", *(f"  {c}" for c in commands),
                "Approving allows these exact commands for the rest of this run only."]
        note = ", ".join(commands)
    elif reason == "no_test_cmd":
        title_end = _TITLES[reason]
        body = ["Set [project] test_cmd in phil.toml, then retry."]
    elif reason == "budget":
        title_end = _TITLES[reason]
        body = [summary]
    elif reason in _TITLES:
        title_end = _TITLES[reason]
        body = problems or [summary]
    else:
        title_end = summary or "a decision"
        body = problems
    kind = "approval" if reason == "approval" else "pause"
    return Decision(kind, f"⏸ {run_id} needs you · {title_end}", _capped(body), options, default, note=note)


def question_decision(index: int, total: int, text: str, why: str, options: list[str]) -> Decision | None:
    if not options:
        return None
    opts = [Option(o, str(n)) for n, o in enumerate(options, 1)]
    opts.append(Option("Something else (type it)", str(len(options) + 1), typed=True))
    opts.append(Option("Plan with what you know", "go"))
    return Decision("question", f"Question {index} of {total}: {text}", _capped([why] if why else []), tuple(opts))


def approach_decision(approaches: list[tuple[str, str]]) -> Decision:
    opts = [Option(f"{name} (recommended)" if n == 1 else name, str(n), detail=summary)
            for n, (name, summary) in enumerate(approaches, 1)]
    opts.append(Option("Describe your own", str(len(approaches) + 1), typed=True))
    return Decision("question", "Pick an approach", (), tuple(opts))


def approval_decision() -> Decision:
    return Decision("confirm", "Approve this plan?", (), (
        Option("Approve and run", "y"), Option("Edit the plan", "edit", typed=True), Option("Cancel", "n")))


def pr_decision(run_id: str, force: bool) -> Decision:
    first = "Open the PR anyway" if force else "Open the PR"
    return Decision("confirm", f"Open a PR for {run_id}?", (), (Option(first, "y"), Option("Not now", "n")))


def fix_decision() -> Decision:
    return Decision("confirm", "Fix it?", (), (
        Option("Quick fix", "y"), Option("Plan it fully", "full"), Option("Not now", "n")))


def replace_decision() -> Decision:
    return Decision("confirm", "Replace the current goal?", (), (
        Option("Replace it", "y"), Option("Keep the current goal", "n")))
```

The plan's interface table lists `settled_prefix`, and the code adds a `note` field. Both are part of the produced interface: Task 5 sets nothing else.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat/test_decision.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Decision model: callout content for every chat question and run pause`, plus the trailer.

---

### Task 2: The failure classifier

**Files:**
- Create: `src/phil/agents/failures.py`, `tests/agents/test_failures.py`

**Interfaces:**
- Consumes: `phil.agents.retry.is_transient`, `provider_detail`; `phil.agents.invoke.ContractViolation`; `phil.config.ConfigError`.
- Produces:
  - `Failure(category: str, headline: str, retries: str, action: str)`, a frozen dataclass with `.as_dict()` and `Failure.from_dict(d)`.
  - `classify_failure(exc: BaseException, *, provider: str | None = None, attempts: int | None = None) -> Failure`. It never raises.
  - `CATEGORIES = ("auth", "quota", "busy", "network", "refused", "output", "config", "internal")`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_failures.py
from phil.agents.failures import Failure, classify_failure
from phil.agents.invoke import ContractViolation
from phil.config import ConfigError


class HTTPError(Exception):
    def __init__(self, status, message="boom"):
        super().__init__(message)
        self.status_code = status


class Response:
    def __init__(self, status):
        self.status_code = status


class ResponseError(Exception):
    def __init__(self, status):
        super().__init__("bad")
        self.response = Response(status)


def test_auth():
    f = classify_failure(HTTPError(401), provider="openrouter")
    assert f.category == "auth"
    assert f.headline == "The provider rejected your API key."
    assert f.action == "Run `phil keys set openrouter`, or set OPENROUTER_API_KEY."


def test_quota_by_status_and_by_wording():
    assert classify_failure(HTTPError(402), provider="openrouter").category == "quota"
    assert classify_failure(HTTPError(400, "Insufficient credits on this account"), provider="openrouter").category == "quota"
    f = classify_failure(HTTPError(402), provider="openrouter")
    assert f.headline == "Your openrouter account is out of credits."


def test_busy_after_retries():
    f = classify_failure(ResponseError(429), provider="anthropic", attempts=4)
    assert (f.category, f.headline, f.retries) == ("busy", "anthropic is busy. Phil retried 4 times.", "Phil already retried.")


def test_network():
    class APIConnectionError(Exception):
        pass
    APIConnectionError.__module__ = "openai"
    f = classify_failure(APIConnectionError("down"), provider="openai")
    assert f.category == "network" and f.headline == "Couldn't reach openai. Phil retried."


def test_refused_carries_the_detail():
    f = classify_failure(HTTPError(400, "model not found"), provider="openrouter")
    assert f.category == "refused"
    assert f.headline.startswith("openrouter refused the request: ")
    assert f.action == "Check the model with `phil models check`."


def test_output_and_config():
    f = classify_failure(ContractViolation("architect", ["no structured output was returned"]))
    assert f.category == "output"
    assert f.headline == "The architect didn't return a usable answer."
    assert f.action == "Try again, or use a stronger model for architect."
    c = classify_failure(ConfigError("No model for critic (tier high)."))
    assert (c.category, c.headline, c.action) == ("config", "No model for critic (tier high).", "Fix the setting it names.")


def test_anything_else_is_internal_and_never_raises():
    class Weird(Exception):
        @property
        def status_code(self):
            raise RuntimeError("nope")

    f = classify_failure(Weird("x"))
    assert f.category == "internal"
    assert f.headline == "Something went wrong inside Phil (Weird)."
    assert f.action == "Details: /more 1"


def test_round_trip():
    f = classify_failure(HTTPError(401), provider="openai")
    assert Failure.from_dict(f.as_dict()) == f
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/agents/test_failures.py -q -n 0`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/phil/agents/failures.py
"""A failure in plain words (spec 2026-10-07 callouts §3.5): what happened, whether Phil retries,
and what the user can do. Built on retry.py's status and transient checks; never raises."""

import re
from dataclasses import asdict, dataclass

CATEGORIES = ("auth", "quota", "busy", "network", "refused", "output", "config", "internal")
_QUOTA_WORDS = re.compile(r"credit|quota|insufficient|billing|payment", re.IGNORECASE)
_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY", "typesafe": "TYPESAFE_API_KEY"}


@dataclass(frozen=True)
class Failure:
    category: str
    headline: str
    retries: str
    action: str

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Failure":
        return cls(**{k: str(data.get(k, "")) for k in ("category", "headline", "retries", "action")})


def _status(exc: BaseException) -> int | None:
    for getter in (lambda e: e.status_code, lambda e: e.response.status_code):
        try:
            value = getter(exc)
        except Exception:
            continue
        if isinstance(value, int):
            return value
    return None


def classify_failure(exc: BaseException, *, provider: str | None = None, attempts: int | None = None) -> Failure:
    try:
        return _classify(exc, provider or "the provider", attempts)
    except Exception:
        return Failure("internal", f"Something went wrong inside Phil ({type(exc).__name__}).",
                       "Phil won't retry this.", "Details: /more 1")


def _classify(exc: BaseException, provider: str, attempts: int | None) -> Failure:
    from phil.agents.invoke import ContractViolation
    from phil.agents.retry import is_transient, provider_detail
    from phil.config import ConfigError

    if isinstance(exc, ContractViolation):
        return Failure("output", f"The {exc.agent} didn't return a usable answer.", "Phil already retried.",
                       f"Try again, or use a stronger model for {exc.agent}.")
    if isinstance(exc, ConfigError):
        return Failure("config", str(exc), "Phil won't retry this.", "Fix the setting it names.")
    status = _status(exc)
    detail = provider_detail(exc) or str(exc)
    if status in (401, 403):
        env = _KEY_ENV.get(provider, f"{provider.upper()}_API_KEY")
        return Failure("auth", "The provider rejected your API key.", "Phil won't retry this.",
                       f"Run `phil keys set {provider}`, or set {env}.")
    if status == 402 or (status is not None and 400 <= status < 500 and _QUOTA_WORDS.search(detail)):
        return Failure("quota", f"Your {provider} account is out of credits.", "Phil won't retry this.",
                       "Add credits, then try again.")
    if status in (429, 529):
        n = f" {attempts} times" if attempts else ""
        return Failure("busy", f"{provider} is busy. Phil retried{n}.", "Phil already retried.",
                       "Try again in a minute.")
    if is_transient(exc):
        return Failure("network", f"Couldn't reach {provider}. Phil retried.", "Phil already retried.",
                       "Check your connection, then try again.")
    if status is not None and 400 <= status < 500:
        return Failure("refused", f"{provider} refused the request: {detail.splitlines()[0][:160]}",
                       "Phil won't retry this.", "Check the model with `phil models check`.")
    return Failure("internal", f"Something went wrong inside Phil ({type(exc).__name__}).",
                   "Phil won't retry this.", "Details: /more 1")
```

**Ruling:** `test_network` builds a fake class whose module is `openai` and whose name is `APIConnectionError`. If `is_transient` matches a different module set for that name (read `retry.py` `_SDK_MODULES`), set `__module__` to one listed there. Keep the assertion.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_failures.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Failure classifier: what happened, whether Phil retries, what to do`, plus the trailer.

---

### Task 3: The callout renderer

**Files:**
- Create: `src/phil/ui/callout.py`, `tests/ui/test_callout.py`
- Modify: `src/phil/ui/theme.py` (callout styles)

**Interfaces:**
- Consumes: `Decision`, `Option` (Task 1); `Failure` (Task 2).
- Produces:
  - `callout_lines(decision: Decision, highlighted: int, width: int, *, live: bool) -> list[list[tuple[str, str]]]`
    - Lines of `(style_name, text)` segments.
    - `live=True` adds the highlight marker `›` and the key-hint line.
    - `live=False` gives numbered options, for `LineIO`.
  - `failure_lines(failure: Failure, width: int) -> list[list[tuple[str, str]]]`
  - `to_rich(lines) -> rich.console.Group` and `to_fragments(lines) -> list[tuple[str, str]]`. Fragments use prompt_toolkit `class:` names derived from the style names, and end with `"\n"` per line.
  - `NARROW = 40`, `KEY_HINT`
  - Style names: `callout.border.<kind>`, `callout.title.<kind>`, `callout.body`, `callout.option`, `callout.selected`, `callout.hint`, `callout.key`. `<kind>` is one of `question`, `approval`, `pause`, `confirm`, `failure`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_callout.py
from rich.cells import cell_len

from phil.agents.failures import Failure
from phil.chat.decision import Decision, Option
from phil.ui.callout import KEY_HINT, callout_lines, failure_lines


def text(lines):
    return ["".join(t for _, t in line) for line in lines]


D = Decision("approval", "⏸ r-1 needs you · approve a command", ("CALC-002 wants to run:", "  node build.mjs"),
             (Option("Approve for this run", "approve"), Option("Deny: the agent continues without it", "deny"),
              Option("Abort the run", "abort")))


def test_live_box_has_a_border_the_highlight_and_the_key_hint():
    lines = text(callout_lines(D, 1, 80, live=True))
    assert lines[0].startswith("╭") and lines[-1].startswith("╰")
    assert any("⏸ r-1 needs you · approve a command" in l for l in lines)
    assert any("› 2 Deny: the agent continues without it" in l for l in lines)
    assert any("  1 Approve for this run" in l for l in lines)
    assert any(KEY_HINT.format(n=3) in l for l in lines)
    assert all(cell_len(l) <= 79 for l in lines)


def test_line_box_is_numbered_without_marker_or_hint():
    lines = text(callout_lines(D, 0, 80, live=False))
    assert any("1 Approve for this run" in l for l in lines)
    assert not any("›" in l for l in lines) and not any("↑/↓" in l for l in lines)


def test_narrow_box_has_no_border_and_fits():
    lines = text(callout_lines(D, 0, 30, live=True))
    assert not lines[0].startswith("╭")
    assert all(cell_len(l) <= 29 for l in lines)


def test_long_text_wraps_in_the_body_and_cuts_in_options():
    long = Decision("question", "Q", ("word " * 60,), (Option("x" * 200, "1"),))
    lines = text(callout_lines(long, 0, 60, live=True))
    assert all(cell_len(l) <= 59 for l in lines)
    assert any("…" in l for l in lines)


def test_markup_like_text_is_literal():
    d = Decision("question", "[bold]Q[/bold]", ("[red]x[/red]",), (Option("[link]a", "1"),))
    lines = text(callout_lines(d, 0, 80, live=True))
    assert any("[bold]Q[/bold]" in l for l in lines) and any("[red]x[/red]" in l for l in lines)


def test_option_detail_is_a_second_line():
    d = Decision("question", "Pick", (), (Option("Inline (recommended)", "1", detail="Add it to calc.py."),))
    lines = text(callout_lines(d, 0, 80, live=True))
    assert any("Add it to calc.py." in l for l in lines)


def test_failure_box():
    f = Failure("quota", "Your openrouter account is out of credits.", "Phil won't retry this.", "Add credits, then try again.")
    lines = text(failure_lines(f, 80))
    joined = "\n".join(lines)
    assert "✗ Your openrouter account is out of credits." in joined
    assert "Phil won't retry this." in joined and "Add credits, then try again." in joined
    assert "Details: /more 1" in joined
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/ui/test_callout.py -q -n 0`
Expected: FAIL.

- [ ] **Step 3: Implement**

`src/phil/ui/callout.py` builds lines from `(style, text)` segments.

- **Box layout:**
  - `╭─ … ─╮` top, `│ <content padded> │` rows, `╰─ … ─╯` bottom;
  - the inner width is `width - 1 - 4`;
  - below `NARROW` there's no border, and every row is indented two spaces with an inner width of `width - 1 - 2`.
- **Rows, in order:**
  1. the title, wrapped, in `callout.title.<kind>`;
  2. the body lines, each wrapped with `textwrap.wrap` on cell widths (keep it simple: wrap by characters and then check `cell_len`), in `callout.body`;
  3. a blank row when there are options;
  4. each option as `› N label` (highlighted, `callout.selected`) or `  N label` (`callout.option`), with the label cut to the inner width with `…` using a `_cut` like `feed_view._cut`; an option's `detail` follows on its own row, indented 4 cells, in `callout.body`;
  5. when `live`, a blank row and then `KEY_HINT.format(n=len(options))` in `callout.hint`.
- **`failure_lines`** builds the same box with kind `failure`. Its rows are `✗ <headline>`, then `<retries> <action>` (wrapped), then `Details: /more 1`.
- **`to_rich`** maps each segment to `Text(t, style=<rich style>)`.
- **`to_fragments`** maps a style name to `f"class:{name}"`.

Add these to `PHIL_THEME` in `src/phil/ui/theme.py`:

```python
        "callout.border.question": "cyan", "callout.border.confirm": "cyan",
        "callout.border.approval": "yellow", "callout.border.pause": "yellow", "callout.border.failure": "red",
        "callout.title.question": "bold cyan", "callout.title.confirm": "bold cyan",
        "callout.title.approval": "bold yellow", "callout.title.pause": "bold yellow", "callout.title.failure": "bold red",
        "callout.body": "default", "callout.option": "default", "callout.selected": "bold reverse",
        "callout.hint": "dim", "callout.key": "blue",
```

Some theme tests check that all style names start with `phil.` (see `STYLE_NAMES` in `theme.py`). If one fails, use `phil.callout.*` everywhere instead. That's a rename, not a design change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ui -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Callout renderer: one layout for the docked prompt, the scrollback and plain lines`, plus the trailer.

---

### Task 4: The terminal's decision mode

**Files:**
- Modify:
  - `src/phil/chat/controller.py`: only the `ChatIO` dataclass and the sentinels.
  - `src/phil/chat/terminal.py`: `TerminalIO.choose`, `LineIO.choose`, and `chat_io` wiring.
- Test: `tests/chat/test_terminal.py`

**Interfaces:**
- Consumes: `Decision` (Task 1); `callout_lines`, `to_fragments`, `to_rich` (Task 3).
- Produces:
  - `phil.chat.controller.TYPE`, a sentinel object like `WAKE`.
  - `ChatIO.choose: Callable[[str, Decision], object] | None = None`
  - `TerminalIO.choose(prompt: str, decision: Decision) -> str | object`, which returns the picked `Option.answer`, or `TYPE`, `WAKE` or None (EOF).
  - `LineIO.choose(prompt, decision)`

- [ ] **Step 1: Write the failing tests**

Use the pipe-input fixtures the existing terminal tests use (`create_pipe_input`, `DummyOutput`). Send key sequences with `pipe.send_text`; prompt_toolkit's escape codes are `"\x1b[B"` (down), `"\x1b[A"` (up), `"\r"` (Enter), `"\x1b"` (Esc).

```python
# add to tests/chat/test_terminal.py, adapted to its fixtures
from phil.chat.controller import TYPE, WAKE
from phil.chat.decision import Decision, Option

D = Decision("pause", "⏸ r-1 needs you · setup failed", ("x",),
             (Option("Retry the task", "retry"), Option("Abort the run", "abort")))


def test_enter_picks_the_default(terminal_io, pipe):
    pipe.send_text("\r")
    assert terminal_io.choose("", D) == "retry"


def test_down_then_enter_picks_the_next_and_up_wraps(terminal_io, pipe):
    pipe.send_text("\x1b[B\r")
    assert terminal_io.choose("", D) == "abort"
    pipe.send_text("\x1b[A\x1b[A\r")
    assert terminal_io.choose("", D) == "abort"  # up twice from 0 wraps to the last, then back... (2 options: 0 -> 1 -> 0)


def test_a_number_picks_at_once(terminal_io, pipe):
    pipe.send_text("2")
    assert terminal_io.choose("", D) == "abort"


def test_typed_letters_are_ignored_in_the_menu(terminal_io, pipe):
    pipe.send_text("zz\r")
    assert terminal_io.choose("", D) == "retry"


def test_escape_returns_type(terminal_io, pipe):
    pipe.send_text("\x1b")
    assert terminal_io.choose("", D) is TYPE


def test_a_wake_keeps_the_highlight(terminal_io, pipe):
    """Move down (highlight 1), trigger terminal_io.wake() from a thread once the prompt is up
    (as the existing wake tests do) -> choose returns WAKE; the next choose("", D) with "\r" returns
    "abort" (the highlight was kept)."""


def test_line_io_choose_prints_numbered_and_maps_numbers(line_io_with_input):
    """LineIO with input "2\n": choose returns "abort", and the printed output contains "1 Retry the task"
    and "2 Abort the run". With input "abort\n": returns "abort". With "anything\n": returns "anything"."""
```

**Ruling:** fix the arithmetic in `test_down_then_enter_picks_the_next_and_up_wraps` against the real wrap rule. With 2 options, up twice from 0 goes 0 → 1 → 0, so Enter returns `retry`. Write the assertion so it checks the wrap correctly, and add a 3-option case where up from 0 lands on index 2. Specs given by docstring must be written with the existing fixtures, and every assertion they state must be made.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/chat/test_terminal.py -q -n 0`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

In `controller.py`, next to `WAKE`:

```python
TYPE = object()  # ChatIO.choose returns this when the user left the menu to type (Esc)
```

Add to `ChatIO`:

```python
    choose: Callable[[str, "Decision"], object] | None = None  # (prompt, decision) -> answer | TYPE | WAKE | None
```

In `terminal.py`, `TerminalIO`:

```python
    def choose(self, prompt: str, decision: Decision) -> object:
        with self._lock:
            if self._wake_pending:
                self._wake_pending = False
                return WAKE
        self._choice = self._carried_choice if self._carried_choice is not None else decision.default
        self._carried_choice = None
        self._decision = decision
        bindings = KeyBindings()
        n = len(decision.options)

        @bindings.add("up")
        def _up(event) -> None:
            self._choice = (self._choice - 1) % n

        @bindings.add("down")
        def _down(event) -> None:
            self._choice = (self._choice + 1) % n

        @bindings.add("enter")
        def _enter(event) -> None:
            event.app.exit(result=decision.options[self._choice].answer)

        for digit in range(1, min(n, 9) + 1):
            @bindings.add(str(digit))
            def _digit(event, index=digit - 1) -> None:
                event.app.exit(result=decision.options[index].answer)

        @bindings.add("escape", eager=True)
        def _escape(event) -> None:
            event.app.exit(result=TYPE)

        @bindings.add("<any>")
        def _ignore(event) -> None:
            pass  # the menu takes no typed text

        try:
            return self.session.prompt(self._decision_message(decision), key_bindings=bindings,
                                       pre_run=self._started)
        except EOFError:
            return None
        finally:
            with self._lock:
                self._active = False
            self._decision = None
            self.session.app.erase_when_done = True  # the docked box never stays in the scrollback
```

- **Wake:** `_exit_with_wake` also saves `self._carried_choice = self._choice` when `self._decision` is set. Initialise `_choice = 0`, `_carried_choice = None` and `_decision = None` in `__init__`.
- **`_decision_message(decision)`** returns a callable that builds the live row (as `_message` does) followed by `to_fragments(callout_lines(decision, self._choice, self.width(), live=True))`. It is guarded like `_message`, and returns an empty message once `self.session.app.is_done`.
- **Key bindings scope:** prompt_toolkit merges the `key_bindings=` passed to `prompt()` with the session's own bindings, and the `<any>` binding must keep typed characters out of the buffer. If the default buffer bindings still insert text, pass the bindings with a higher priority, using `merge_key_bindings` or `prompt(..., key_bindings=...)` plus `self.session.default_buffer.read_only` set to a `Condition` that's true while choosing. The tests decide; keep them strict.
- **`chat_io`** returns `ChatIO(ask=self.ask, choose=self.choose, …)`.

`LineIO.choose(prompt, decision)`:
- prints `to_rich(callout_lines(decision, decision.default, self.console.width, live=False))`;
- reads a line, as `LineIO.ask` does;
- a digit `k` with `1 <= k <= len(options)` returns `options[k-1].answer`, and any other text is returned unchanged;
- EOF returns None.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat tests/cli -q -n 0`
Expected: PASS. Existing fakes build `ChatIO(ask=…, spawn=…)` without `choose`, so they're unaffected.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Terminal decision mode: a docked callout answered with arrow keys`, plus the trailer.

---

### Task 5: The controller: callouts for every decision, and failure callouts

**Files:**
- Modify:
  - `src/phil/chat/controller.py`
  - `src/phil/run/worker.py`: a `failure` event on a crashed run.
  - `README.md`: the chat section.
- Test: `tests/chat/test_controller_callouts.py` (new), plus updates to existing chat tests that assert the old prompt text only where a stage now prints a callout. Use the existing controller harness.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `ChatController._decision() -> Decision | None`
  - `ChatController._folded: bool`, true after `TYPE`, cleared by `/answer` or a new decision.
  - A run event `failure {category, headline, retries, action}`, written by the worker when the run crashes with an exception.

- [ ] **Step 1: Write the failing tests** (docstring-specified; use the harness that builds a `ChatController` with a fake `ChatIO` and a recording console; read `tests/chat/test_controller*.py`)

```python
# tests/chat/test_controller_callouts.py
def test_decision_per_stage(controller):
    """With stage forced (and the needed state set) to each of questions (a question with options),
    choose_approach, approval, confirm_pr, confirm_fix, confirm_replace and paused (escalation reason
    approval), _decision() returns a Decision whose answers are exactly those of the matching Task 1
    builder; for idle, hint, edit, running, and questions while _typing_other, it returns None."""


def test_the_loop_uses_choose_and_prints_the_settled_line(controller_with_choose):
    """A fake ChatIO whose choose returns "y" at the approval stage: the console shows
    "✓ Approve and run", and the run is launched exactly as typing "y" would."""


def test_type_folds_and_answer_reopens(controller_with_choose):
    """At a paused stage choose returns TYPE: the next io call is ask() (not choose), with a prompt that
    starts with "⏸ r-… needs you · /answer to choose"; typing "/answer" makes the following call choose() again."""


def test_a_typed_option_goes_to_its_typed_prompt(controller_with_choose):
    """At the questions stage choose returns the "Something else" answer (n+1): the next call is ask()
    with the existing OTHER_PROMPT; its reply becomes the answer."""


def test_answered_elsewhere_closes_the_callout(controller_with_choose):
    """While paused (callout open), a run_resumed event arrives: the console shows
    "r-… was answered elsewhere." and the next io call is ask() with the running prompt."""


def test_job_failure_prints_a_failure_callout_and_more_1_shows_the_raw_error(controller):
    """A job_failed event carrying failure={category: quota, headline: "Your openrouter account is out
    of credits.", retries: "Phil won't retry this.", action: "Add credits, then try again."} and
    error="OpenRouterError: 402 …": the console shows the ✗ headline, the retries line and the action,
    and NOT the text "OpenRouterError"; then "/more 1" prints the raw error text."""


def test_a_job_failure_without_failure_data_still_reports(controller):
    """A job_failed event with only error= (old shape): an internal-category callout is shown."""


def test_a_crashed_run_shows_its_failure_callout(controller_following_a_run):
    """run_done with state failed, and the run's events.jsonl holding a failure event: the failure
    callout is printed with its headline."""
```

**Ruling:** these are specified by docstring, because the controller harness is large. Make every assertion stated.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/chat/test_controller_callouts.py -q -n 0`
Expected: FAIL.

- [ ] **Step 3: Implement**

**`_decision()`:**
- `questions` (not `_typing_other`): `question_decision(i, n, q.text, q.why, q.options)`, where `q` is the current pending question.
- `choose_approach` (not `_describing`): `approach_decision([(a.name, a.summary) for a in self._approach_order])`.
- `approval`: `approval_decision()`.
- `confirm_pr`: `pr_decision(run_id, self._pr_force)`.
- `confirm_fix`: `fix_decision()`.
- `confirm_replace`: `replace_decision()`.
- `paused` with `self._pause`: `pause_decision(self._run_id, self._pause)`.
- Anything else: None.
- Return None while `self._folded` is set.

**The loop** (around controller.py:465):

```python
                decision = self._decision()
                if decision is not None and self.io.choose is not None:
                    raw = self.io.choose(self._prompt(), decision)
                    if raw is TYPE:
                        self._folded = True
                        continue
                    if isinstance(raw, str):
                        option = next((o for o in decision.options if o.answer == raw), None)
                        if option is not None and not option.typed:  # a typed option's answer comes next
                            self.console.print(Text(settled_line(decision, option), style="phil.muted"))
                else:
                    raw = self.io.ask(self._folded_prompt() if self._folded else self._prompt())
```

The existing `WAKE` and None handling and `_input(raw)` follow unchanged.

- `_folded_prompt()` returns `f"{reminder} · /answer to choose\n{self._prompt()}"`. The reminder is the decision's title for pauses and approvals, `Question i of n` for questions, and the decision's title for the others.
- `_folded` is cleared:
  - in `_set_stage` whenever the stage changes;
  - in the `/answer` handler. `/answer` also keeps its existing behaviour when nothing is folded.
- The existing printed menus become redundant when `self.io.choose` is set:
  - `_ask_next`'s numbered list;
  - the approaches list's numbers;
  - the pause line's options.

  Keep printing them only when `self.io.choose is None` (LineIO and fakes), so piped use is unchanged.
- **A capped body** (its last line is `… see /more 1`): when the controller builds that decision, it writes the full body (for a pause, the summary and every problem, one per line) to `self.session.dir / "decision.txt"`, and sets `self._last_refs = [Ref(label="details", path=...)]`, so `/more 1` shows it. Add a test: a review_failed pause with 20 problems, then `/more 1` prints all 20.
- **Answered elsewhere:** in `_on_run_resumed`, if the stage was `paused` and the answer didn't come from this chat (`not self._answer_sent`), print `answered_elsewhere(run_id)`.

**Failure callouts:**
- **`_job.work`**, on an exception: add `"failure": classify_failure(exc, provider=<the provider of the role's model if easily known, else None>).as_dict()` to the event data. Wrap the classification in try/except, so a failure there never blocks the post.
- **`_report(exc)`:** classify directly.
- **`_failed(error, failure=None)`:**
  - print `to_rich(failure_lines(Failure.from_dict(failure) if failure else <internal Failure>, width))`;
  - write the raw `error` to `self.session.dir / "last_error.txt"` (UTF-8, `\n`);
  - set `self._last_refs = [Ref(label="error", path=<that file>)]` and `self._refs_from_btw = False`;
  - keep the `_safe_note("error", …)`;
  - drop the old `Phil couldn't finish that:` line and `Details:` path line, since the callout replaces them.
- **`_on_job_failed`:** `self._failed(data["error"], data.get("failure"))`.
- **Worker:** in `run_worker`'s generic exception path (where a run is marked `failed`), append `events.append("failure", **classify_failure(exc).as_dict())` inside a try.
- **Chat, on a failed run:** in `_run_notice`'s failed branch, read `run_events(paths, run_id).latest("failure")` and print its failure callout. The ref `/more 1` is the run's worker log.

**README:** in the chat section, add that questions, approvals and pauses appear as callouts you answer with ↑/↓ and Enter (Esc to type instead, `/answer` to reopen), and that failures say what happened and what to do.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat tests/cli tests/run -q -n 0`
Expected: PASS. A test asserting `Phil couldn't finish that:` should now assert the callout's headline instead. Adapt it, keeping its intent.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`.
Commit message: `Chat callouts: every decision as an arrow-key menu, and failures in plain words`, plus the trailer.
