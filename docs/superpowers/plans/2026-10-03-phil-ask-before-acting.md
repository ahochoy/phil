# Ask Before Acting (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phil asks before it acts:
- intake's clarifying questions are multiple choice, asked one at a time;
- intake must ask for content only the user can supply;
- on the full path, when the approach is open, a read-only designer proposes 2–3 approaches for the user to pick from before the architect plans.

**Architecture:**
- **Questions:**
  - `Goal.open_questions` becomes `list[Question]`. Plain strings are still accepted and become free-text questions.
  - The chat controller walks the questions locally, one per input, and makes a single intake follow-up call with every answer.
- **Designer:**
  - A new `design` agent spec (role `designer`, high tier) on the light read-only harness returns `Approaches`.
  - The controller shows them as one choice and passes the pick (or the user's own description) to the architect through `ArchitectInput`.

**Tech Stack:** Python 3.14, pydantic v2, LangChain light harness (`phil.agents`), rich console, pytest (offline, `ScriptedAgentFactory` / `ChatFactory`).

**Spec:** `docs/superpowers/specs/2026-10-03-phil-ask-before-acting-design.md` (§3 and §5; §4 is plan B).

## Global Constraints

- **Editing files:** use only the Edit and Write tools. Never edit through python, perl, sed, heredocs or printf in Bash.
- **Commits:** write the message to a file with Write, then run `git commit -F <file>`. The message ends with a blank line and then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Bash command lines:** keep the words "keychain" and "credentials" out of them.
- **Hooks and guards:** never work around one. If you are blocked, stop and report BLOCKED.
- **Secrets:** never read or print `.env` files or key values.
- **Tests you may run:**
  - never run `-m live` or `-m bench`;
  - iterate with `uv run pytest <paths> -q -n 0`;
  - run the full `uv run pytest -q` once at the end of each task.
- **User-facing strings:** use these exact strings verbatim.

  | Name | String |
  |---|---|
  | `SOMETHING_ELSE` | `Something else (type it)` |
  | `OTHER_PROMPT` | `Your answer › ` |
  | `DESCRIBE_OWN` | `Describe your own` |
  | `DESCRIBE_PROMPT` | `Describe your approach › ` |
  | `DESIGN_FALLBACK` | `Couldn't propose designs; planning directly.` |
  | `PROMPTS["choose_approach"]` | `Pick an approach [Enter = 1 / number / describe your own] › ` |

- **Limits:**
  - a question has 0 or 2–4 options;
  - `Approaches` has 2–3 options;
  - `DESIGN_MAX_MODEL_CALLS = 8`;
  - `MAX_QUESTION_ROUNDS` stays 2.
- **Designer model:**
  - role `designer`, default tier `high`, default input budget 24 000 tokens;
  - without a designer model (a legacy per-role config), it uses the architect's;
  - it isn't a `CHAT_ROLES` member, but its key is checked like the answerer's.
- **When the designer runs:** only on the full path, when `goal.approach_open` is true, no open questions remain, and the message didn't start with `/full!` (any case).

## Review Focus

1. **Option numbers with punctuation:** a reply of `2.` or `2)` picks option 2, as a person would expect. Tested in Task 2.
2. **Digits at a free-text question:** a digit reply to a question with no options (e.g. `42`) is the answer, not an option number. Tested in Task 2.
3. **Ctrl-C after "Something else":** Ctrl-C while typing a "Something else" answer cancels the whole goal and leaves nothing half-asked. Tested in Task 2.
4. **Out-of-range recommendation:** a designer whose `recommended` index is out of range doesn't crash the chat; the first approach is recommended. Tested in Task 1.
5. **Case of `/full!`:** `/FULL!` skips the designer exactly as `/full!` does. Tested in Task 4.

## Rulings made while planning

- **R1, option normalisation:** a model's slightly-off option list is normalised, not rejected:
  - blanks are dropped;
  - the list is cut to 4;
  - a lone option becomes a free-text question.

  `Approaches.options` is likewise cut to 3, and an out-of-range `recommended` becomes 0. This saves a retry on an otherwise good output (spec §5 "options must number 0 or 2–4" holds after normalisation).
- **R2, `go` after partial answers:** typing `go` after some answers sends the answers given so far in one intake call, with no further question rounds (`_rounds` is set to the maximum). This keeps what the user already said. With no answers yet, `go` plans straight away, as today.
- **R3, Enter at the approach choice:** Enter picks the recommended approach (shown as 1). This is a small convenience beyond the spec, and the prompt says so.
- **R4, the design-proposals line:** `/full!` is shown on the status line as `Forced: full path, no design proposals`.

---

### Task 1: Question and approach contracts, intake prompt

**Files:**
- Modify: `src/phil/contracts/interface.py`. Add `Question`; change `Goal.open_questions` and add a coercion validator; add `Goal.approach_open`.
- Create: `src/phil/contracts/design.py`, holding `Approach`, `Approaches` and `DesignInput`.
- Modify: `src/phil/contracts/inputs.py`. `ArchitectInput` gains `chosen_approach` and `approach_note`.
- Modify: `src/phil/contracts/__init__.py`. Export the new names, and add `Approaches` and `DesignInput` to `ALL_CONTRACTS`.
- Modify: `src/phil/prompts/intake.md`
- Modify: `src/phil/ui/plan_view.py:24-30`. Questions render `.text`.
- Modify: `src/phil/chat/controller.py:746-747`. The existing question print uses `question.text`; Task 2 rewrites this block.
- Test: `tests/test_ask_contracts.py` (new), `tests/test_contract_descriptions.py`, `tests/chat/test_intake_spec.py`, `tests/chat/test_planning.py:13`

**Interfaces:**
- Produces:
  - `phil.contracts.Question(text: str, options: list[str] = [], why: str = "")`
  - `Goal.open_questions: list[Question]`, which also accepts `list[str]`
  - `Goal.approach_open: bool = False`
  - `phil.contracts.Approach(name: str, summary: str, tradeoffs: list[str] = [])`
  - `phil.contracts.Approaches(options: list[Approach], recommended: int = 0, reason: str = "")`
  - `phil.contracts.DesignInput(goal: Goal, repo_overview: str = "")`
  - `ArchitectInput.chosen_approach: Approach | None = None`, `ArchitectInput.approach_note: str = ""`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ask_contracts.py`:

```python
import pytest
from pydantic import ValidationError

from phil.contracts import ALL_CONTRACTS, Approach, Approaches, ArchitectInput, DesignInput, Goal, Question
from tests.test_contract_descriptions import missing_descriptions

APPROACH = Approach(name="Footer band", summary="A full-width band above the footer.", tradeoffs=["On every page"])


def test_a_plain_string_question_is_free_text():
    goal = Goal(objective="Add a CTA section", open_questions=["Which page?"])
    assert goal.open_questions == [Question(text="Which page?", options=[])]


def test_a_saved_goal_with_string_questions_still_loads_and_round_trips():
    goal = Goal.model_validate({"objective": "Add a CTA section", "open_questions": ["Which page?"]})
    assert Goal.model_validate(goal.model_dump(mode="json")) == goal


def test_options_are_none_or_two_to_four():
    assert Question(text="q", options=["only one"]).options == []
    assert Question(text="q", options=["a", "  ", "b"]).options == ["a", "b"]
    assert Question(text="q", options=list("abcdef")).options == list("abcd")
    assert Question(text="q").options == []


def test_goal_approach_open_defaults_false():
    assert Goal(objective="Add a CTA section").approach_open is False


def test_approaches_need_two_keep_three_and_a_valid_recommendation():
    with pytest.raises(ValidationError):
        Approaches(options=[APPROACH])
    out = Approaches(options=[APPROACH] * 4, recommended=7)
    assert len(out.options) == 3 and out.recommended == 0
    assert Approaches(options=[APPROACH] * 2, recommended=1).recommended == 1


def test_new_parts_describe_every_field():
    assert missing_descriptions(Question) == []
    assert missing_descriptions(Approaches) == []
    props = ArchitectInput.model_json_schema()["properties"]
    assert "description" in props["chosen_approach"] and "description" in props["approach_note"]
    goal_props = Goal.model_json_schema()["properties"]
    assert "description" in goal_props["open_questions"] and "description" in goal_props["approach_open"]


def test_design_contracts_are_registered():
    assert Approaches in ALL_CONTRACTS and DesignInput in ALL_CONTRACTS
    assert DesignInput(goal=Goal(objective="Add a CTA section")).repo_overview == ""
```

Append to `tests/chat/test_intake_spec.py`:

```python
def test_intake_prompt_asks_for_missing_content_with_options():
    prompt = load_prompt(get_spec("intake"))
    assert "## When to ask" in prompt
    for word in ("link", "copy", "placement", "visual style", "quick"):
        assert word in prompt
    assert "`options`" in prompt and "`approach_open`" in prompt
```

In `tests/chat/test_planning.py` line 13, change `assert first.open_questions == ["Which file?"]` to `assert first.open_questions == [Question(text="Which file?")]`, and add `from phil.contracts import Question` to its imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ask_contracts.py tests/chat/test_intake_spec.py tests/chat/test_planning.py -q -n 0`
Expected: FAIL. The import of `Approach` and `Question` fails, and the prompt test fails.

- [ ] **Step 3: Implement the contracts**

In `src/phil/contracts/interface.py`, add before `class Goal`:

```python
MAX_OPTIONS = 4


class Question(Part):
    text: str = Field(description="The question, in one sentence.")
    options: list[str] = Field(
        default=[],
        description="2 to 4 short answers the user can pick from, most likely first; empty only when no sensible "
        "options exist. Don't add an \"other\" option: the chat adds one.",
    )
    why: str = Field(default="", description="Optional: one short line on what the answer changes.")

    @field_validator("options")
    @classmethod
    def _usable_options(cls, value: list[str]) -> list[str]:
        """0 or 2–4 options: blanks are dropped, extras cut, and a lone option makes it a free-text
        question (a slightly-off list shouldn't cost a retry)."""
        options = [option.strip() for option in value if option.strip()][:MAX_OPTIONS]
        return options if len(options) >= 2 else []
```

In `Goal`, replace `open_questions: list[str] = []` with:

```python
    open_questions: list[Question] = Field(
        default=[],
        description="Questions whose answer would change the work, most important first; at most 3.",
    )
    approach_open: bool = Field(
        default=False,
        description="True when there are several reasonable ways to build this (layout, structure, library) and "
        "the user hasn't said which.",
    )
```

Then add this validator to `Goal`, next to `_check_objective_is_real`:

```python
    @field_validator("open_questions", mode="before")
    @classmethod
    def _questions_from_text(cls, value: object) -> object:
        """A plain string is a free-text question, so saved chats and older outputs still load."""
        if isinstance(value, list):
            return [{"text": item} if isinstance(item, str) else item for item in value]
        return value
```

Create `src/phil/contracts/design.py`:

```python
from pydantic import Field, field_validator, model_validator

from phil.contracts.base import Contract, Part
from phil.contracts.interface import Goal

MAX_APPROACHES = 3


class Approach(Part):
    name: str = Field(description="A short name, 2 to 5 words.")
    summary: str = Field(description="One or two sentences: what gets built and where, in the repo's terms.")
    tradeoffs: list[str] = Field(default=[], description="1 to 3 short trade-offs: costs, risks, what it rules out.")


class Approaches(Contract):
    options: list[Approach] = Field(min_length=2, description="2 or 3 genuinely different ways to build the goal.")
    recommended: int = Field(default=0, description="The index in `options` (from 0) of the approach you recommend.")
    reason: str = Field(default="", description="One sentence: why you recommend it.")

    @field_validator("options")
    @classmethod
    def _at_most_three(cls, value: list[Approach]) -> list[Approach]:
        return value[:MAX_APPROACHES]

    @model_validator(mode="after")
    def _recommended_in_range(self) -> "Approaches":
        if not 0 <= self.recommended < len(self.options):
            self.recommended = 0
        return self


class DesignInput(Contract):
    goal: Goal
    repo_overview: str = ""
```

In `src/phil/contracts/inputs.py`, import `Approach` from `phil.contracts.design`, and add these fields to `ArchitectInput` after `prior_attempt`:

```python
    chosen_approach: Approach | None = Field(
        default=None, description="When set, the user chose this approach from the designer's proposals: plan it."
    )
    approach_note: str = Field(
        default="", description="When set, the user's own description of how to build it: plan that."
    )
```

In `src/phil/contracts/__init__.py`:
- import `Question` from `interface`;
- import `Approach`, `Approaches` and `DesignInput` from `phil.contracts.design`;
- append `Approaches` and `DesignInput` to `ALL_CONTRACTS`;
- add all four names to `__all__`, keeping it alphabetical.

- [ ] **Step 4: Update the intake prompt and the views**

In `src/phil/prompts/intake.md`, replace the `open_questions` bullet with:

```markdown
- `open_questions`: questions whose answer would change the work, most important first. At most 3. Each has:
  - `text`: the question, in one sentence.
  - `options`: 2–4 short answers the user can pick from, drawn from the repo and the request (for example, sections that exist on the page, or "placeholder links for now"). Leave it empty only when no sensible options exist. Never add an "other" option; the chat adds one.
  - `why`: optional, one short line on what the answer changes.
  Leave `open_questions` empty when the goal is clear enough to do; a planner can make reasonable choices on minor details.
- `approach_open`: true when there are several reasonable ways to build this (layout, structure, data model, library) and the user hasn't said which; false for a clear change with one obvious way.
```

Insert this section after the `## Goal fields` list and before `## Follow-ups`:

```markdown
## When to ask
- Ask before any work is planned or written when the change depends on something only the user can supply:
  - link targets or URLs;
  - copy, product or offer names and their details;
  - images or other assets;
  - which page or section;
  - placement on the page;
  - visual style, when the change is visual.
- This applies on every path, `quick` included. While questions are open, `task` stays null.
- Don't invent this content and don't plan placeholders unless the user chose placeholders.
```

In `src/phil/ui/plan_view.py` `render_goal`, change `escape(_clip(question))` to `escape(_clip(question.text))`.

In `src/phil/chat/controller.py` `_on_goal_ready`, change `escape(_clip(question))` to `escape(_clip(question.text))`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ask_contracts.py tests/chat/test_intake_spec.py tests/chat/test_planning.py tests/test_contract_descriptions.py tests/ui/test_plan_view.py tests/test_contracts.py tests/test_cli.py -q -n 0`
Expected: PASS. If a schema-export test pins a count or a list of names, update it to include `Approaches` and `DesignInput`.

- [ ] **Step 6: Run the full suite, then commit**

Run: `uv run pytest -q`
Expected: all pass.

Commit message: `Make intake's questions multiple choice and add design contracts`, plus the trailer.

---

### Task 2: The chat asks questions one at a time

**Files:**
- Modify: `src/phil/chat/controller.py`:
  - constants near line 66;
  - `__init__`;
  - `_reset_goal`;
  - `_prompt`;
  - `_on_goal_ready` (around line 740);
  - `_answers` (around line 807).
- Test: `tests/chat/test_questions_flow.py` (new), `tests/chat/test_routing_flow.py:363-381`

**Interfaces:**
- Consumes: `Question` (Task 1).
- Produces:
  - controller constants `SOMETHING_ELSE` and `OTHER_PROMPT`;
  - controller attributes `_pending: list[Question]`, `_replies: list[str]` and `_typing_other: bool`;
  - methods `_ask_next()`, `_pick(question, text) -> str | None` and `_finish_questions()`;
  - intake follow-up `answers` lines in the form `"{question.text}: {answer}"`, also kept in `_clarifications`.

- [ ] **Step 1: Write the failing tests**

Create `tests/chat/test_questions_flow.py`:

```python
"""Intake's questions are asked one at a time, as multiple choice (spec §3.3)."""

from phil.contracts import Question
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
from tests.chat.test_quick_flow import detectable, quick_goal
from tests.chat.test_routing_flow import payloads, peek, route

PLACEMENT = Question(text="Where should the CTA go?", options=["Above the footer", "After the hero"], why="Changes the layout.")
LINKS = Question(text="Where do the buttons link?", options=["Existing pages", "Placeholder links for now"])
COPY = Question(text="What should the heading say?")
PLANNING = {"architect": [plan()], "critic": [critique()]}


def shown(seen, key, reply):
    """A script item that records the console so far, then replies."""

    def look(controller):
        seen[key] = controller.console.export_text(clear=False)
        return reply

    return look


def test_questions_are_asked_one_at_a_time_then_intake_gets_every_answer(calc_repo):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", shown(seen, "first", "1"), shown(seen, "second", "Placeholder links for now"), "Book a call", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS, COPY]), goal()], **PLANNING},
    )
    assert "1. Where should the CTA go?" in seen["first"] and "Changes the layout." in seen["first"]
    assert "1. Above the footer" in seen["first"] and "3. Something else (type it)" in seen["first"]
    assert "Where do the buttons link?" not in seen["first"]
    assert "2. Where do the buttons link?" in seen["second"] and "What should the heading say?" not in seen["second"]
    assert "3. What should the heading say?" in text
    first, second = payloads(factory, "intake")  # one follow-up call, after the last answer
    assert "Where should the CTA go?: Above the footer" in second
    assert "Where do the buttons link?: Placeholder links for now" in second
    assert "What should the heading say?: Book a call" in second


def test_something_else_asks_for_the_text(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "3", "Right under the pricing table", "2", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS]), goal()], **PLANNING},
    )
    assert "Your answer › " in prompts
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: Right under the pricing table" in second
    assert "Where do the buttons link?: Placeholder links for now" in second


def test_option_numbers_with_punctuation_pick_the_option(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "2.", "1)", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS]), goal()], **PLANNING},
    )
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: After the hero" in second
    assert "Where do the buttons link?: Existing pages" in second


def test_an_out_of_range_number_asks_again(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "9", "1", "n"],
        {"intake": [goal(open_questions=[PLACEMENT]), goal()], **PLANNING},
    )
    assert "Pick 1–3, or type your answer." in text
    assert "Where should the CTA go?: Above the footer" in payloads(factory, "intake")[1]


def test_typed_text_and_digits_at_a_free_text_question_are_the_answer(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "Somewhere visible", "42", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, COPY]), goal()], **PLANNING},
    )
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: Somewhere visible" in second
    assert "What should the heading say?: 42" in second


def test_go_after_some_answers_sends_them_and_asks_nothing_more(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "1", "go", "n"],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS]), goal(open_questions=[COPY])], **PLANNING},
    )
    first, second = payloads(factory, "intake")
    assert "Where should the CTA go?: Above the footer" in second
    assert "1. What should the heading say?" not in text  # the second round isn't asked
    assert "Planning with open questions: 1" in text


def test_go_before_any_answer_plans_straight_away(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "go", "n"], {"intake": [goal(open_questions=[PLACEMENT])], **PLANNING}
    )
    assert len(payloads(factory, "intake")) == 1
    assert "Planning with open questions: 1" in text


def test_ctrl_c_while_typing_something_else_cancels_the_goal(calc_repo):
    seen = {}

    def ctrl_c(controller):
        raise KeyboardInterrupt

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "3", ctrl_c, peek(seen)],
        {"intake": [goal(open_questions=[PLACEMENT, LINKS])], **PLANNING},
    )
    assert "Cancelled the current goal." in text
    assert seen["stage"] == "idle"
    assert len(payloads(factory, "intake")) == 1 and spawned == []


def test_the_cta_request_is_asked_about_before_a_quick_task_is_written(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section at the bottom of the page", "1", "2"],
        {"route": [route("simple_change")], "intake": [goal(open_questions=[PLACEMENT, LINKS]), quick_goal()]},
    )
    assert text.index("Where should the CTA go?") < text.index("Where do the buttons link?") < text.index("Quick change:")
    second = payloads(factory, "intake")[1]
    assert "Where should the CTA go?: Above the footer" in second
    assert "Where do the buttons link?: Placeholder links for now" in second
    assert len(spawned) == 1
```

In `tests/chat/test_routing_flow.py`, `test_clarifications_reach_the_answer_when_intake_chooses_one`: change `"Clarification: the login page" in answer` to `"Clarification: Which page?: the login page" in answer`. Leave the `seen["recent"]` assertion unchanged.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/chat/test_questions_flow.py -q -n 0`
Expected: FAIL. No option lines are printed, and all the questions print at once.

- [ ] **Step 3: Implement**

In `src/phil/chat/controller.py`, add these constants next to `MAX_QUESTION_ROUNDS`:

```python
SOMETHING_ELSE = "Something else (type it)"
OTHER_PROMPT = "Your answer › "  # after picking "Something else"
```

Add `Question` to the `phil.contracts` import. In `__init__`, next to `self._rounds = 0`:

```python
        self._pending: list[Question] = []  # this round's questions, asked one at a time
        self._replies: list[str] = []  # "question: answer" for the questions answered so far
        self._typing_other = False  # "Something else" was picked: the next input is the answer
```

In `_reset_goal`, add `self._pending, self._replies, self._typing_other = [], [], False`.

In `_prompt`, before the final `return`:

```python
        if self.stage == "questions" and self._typing_other:
            return OTHER_PROMPT
```

In `_on_goal_ready`, replace the question block with:

```python
        if goal.open_questions and self._rounds < MAX_QUESTION_ROUNDS:
            self._rounds += 1
            self._pending, self._replies, self._typing_other = list(goal.open_questions), [], False
            self._set_stage("questions")
            self._ask_next()
            return
```

Replace `_answers` with the following, and add the helpers:

```python
    def _ask_next(self) -> None:
        """Print the next question: its number, text, why, and numbered options plus "Something else"."""
        question = self._pending[len(self._replies)]
        self.console.print(f"[phil.agent]{len(self._replies) + 1}. {escape(_clip(question.text))}[/]")
        if question.why:
            self.console.print(f"   [phil.muted]{escape(_clip(question.why))}[/]")
        for n, option in enumerate(question.options, 1):
            self.console.print(f"     {n}. {escape(_clip(option))}")
        if question.options:
            self.console.print(f"     {len(question.options) + 1}. {SOMETHING_ELSE}")

    def _answers(self, text: str) -> None:
        """One reply to the current question. No model call until the last one is answered; then one
        intake call gets every answer. `go` plans with what's known (Ruling R2)."""
        if not text:
            return
        if text.lower() == "go" and not self._typing_other:
            if self._replies:
                self._rounds = MAX_QUESTION_ROUNDS  # send what was answered; ask nothing more
                self._finish_questions()
            else:
                self._build(self._goal)
            return
        question = self._pending[len(self._replies)]
        answer = self._pick(question, text)
        if answer is None:
            return
        self._typing_other = False
        self._replies.append(f"{question.text}: {answer}")
        self._recent.append(f"you: {answer[:TURN_CHARS]}")
        if len(self._replies) < len(self._pending):
            self._ask_next()
        else:
            self._finish_questions()

    def _pick(self, question: Question, text: str) -> str | None:
        """The answer `text` gives `question`, or None while it still needs one (the "Something else"
        number was picked, or a number with no option). Free-text questions take any reply."""
        token = text.rstrip(".)")
        if self._typing_other or not question.options or not token.isdigit():
            return text
        choice = int(token)
        if 1 <= choice <= len(question.options):
            return question.options[choice - 1]
        if choice == len(question.options) + 1:
            self._typing_other = True
            return None
        self.console.print(f"Pick 1–{len(question.options) + 1}, or type your answer.")
        return None

    def _finish_questions(self) -> None:
        replies, self._pending, self._replies = self._replies, [], []
        self._clarifications.extend(replies)
        self._set_stage("intake")
        self._intake_job(self._goal_text, previous=self._goal, answers=replies)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat/test_questions_flow.py tests/chat/test_routing_flow.py tests/chat/test_quick_flow.py tests/chat/test_controller.py tests/chat/test_reopen_state.py -q -n 0`
Expected: PASS. Fix any older assertion that expected the raw answer as intake's `answers` entry, so it expects the `"{question}: {answer}"` form. Don't weaken anything else.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`
Expected: all pass.

Commit message: `Ask intake's questions one at a time, as multiple choice`, plus the trailer.

---

### Task 3: The designer agent

**Files:**
- Modify: `src/phil/config.py:11-30`. Add the `designer` role, its tier and its budget.
- Modify: `src/phil/cli/main.py:215-217`. Check the designer's key.
- Modify: `src/phil/agents/spec.py`. Add `PROPOSE_NOW`.
- Modify: `src/phil/agents/registry.py`. Add `DESIGN_MAX_MODEL_CALLS` and the `"design"` spec.
- Create: `src/phil/prompts/design.md`
- Create: `src/phil/chat/design.py`, holding `propose_approaches`.
- Modify: `src/phil/store/telemetry.py:134`. Add `GOAL_NODES` `"design"`.
- Test: `tests/chat/test_design.py` (new), `tests/agents/test_registry.py`, `tests/chat/test_intake_spec.py:33-36`, `tests/test_config.py`

**Interfaces:**
- Consumes: `Approaches` and `DesignInput` (Task 1).
- Produces:
  - `phil.chat.design.propose_approaches(ctx: AgentContext, goal: Goal, *, tree: Path, overview: str, call: int = 1) -> Approaches`
  - `phil.agents.registry.DESIGN_MAX_MODEL_CALLS = 8`
  - spec name `"design"`, role `"designer"`, telemetry node `"design"`

- [ ] **Step 1: Write the failing tests**

Create `tests/chat/test_design.py`:

```python
from phil.agents.fake import FakeAgentFactory, ScriptedAgentFactory, Turn
from phil.agents.invoke import AgentContext
from phil.agents.registry import DESIGN_MAX_MODEL_CALLS, get_spec
from phil.agents.spec import load_prompt
from phil.agents.tools import CommandLog
from phil.chat.design import propose_approaches
from phil.config import CHAT_ROLES, DEFAULT_TIERS, ROLES, PhilConfig
from phil.contracts import Approach, Approaches, DesignInput, Goal
from phil.store.db import connect

GOAL = Goal(objective="Add a CTA section to the home page", approach_open=True)
APPROACHES = Approaches(
    options=[Approach(name="Footer band", summary="A band above the footer."), Approach(name="Inline card", summary="A card after the hero.")],
    recommended=1,
)
LEGACY_SIX = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")


def ctx_for(tmp_path, config, factory):
    return AgentContext(config=config, conn=connect(tmp_path / "t.db"), layer="chat", factory=factory)


def test_the_designer_reads_the_tree_and_returns_approaches(tmp_path):
    seen: list[Turn] = []

    def design(turn: Turn) -> Approaches:
        seen.append(turn)
        return APPROACHES

    config = PhilConfig(models={"low": "openrouter:l", "high": "openrouter:h"})
    out = propose_approaches(ctx_for(tmp_path, config, ScriptedAgentFactory({"design": [design]})), GOAL, tree=tmp_path, overview="Tracked files:\nindex.html")
    assert out == APPROACHES
    assert seen[0].workdir == tmp_path and set(seen[0].tools) == {"run_shell"}
    packet = seen[0].payload["messages"][0]["content"]
    assert "Add a CTA section to the home page" in packet and "Tracked files:" in packet


def test_the_design_spec_is_light_read_only_and_capped():
    spec = get_spec("design")
    assert (spec.role, spec.harness, spec.read_only_shell, spec.writes_files) == ("designer", "light", True, False)
    assert (spec.in_contract, spec.out_contract) == (DesignInput, Approaches)
    assert spec.max_model_calls == DESIGN_MAX_MODEL_CALLS == 8
    assert load_prompt(spec).startswith("# Role: Designer")


def test_the_designer_shell_cannot_write(tmp_path):
    from phil.agents.invoke import _shell_for

    run = _shell_for(get_spec("design"), PhilConfig(models={"high": "openrouter:h"}), tmp_path, CommandLog(), approved=("touch y",))
    assert run("ls").startswith("exit_code: 0")
    assert run("touch y").startswith("DENIED")


def test_the_designer_role_resolves_through_the_high_tier():
    assert "designer" in ROLES and DEFAULT_TIERS["designer"] == "high"
    assert "designer" not in CHAT_ROLES
    assert PhilConfig(models={"high": "openrouter:h", "low": "openrouter:l"}).model_for("designer") == "openrouter:h"


def test_without_a_designer_model_the_designer_uses_the_architects(tmp_path):
    factory = FakeAgentFactory([APPROACHES])
    config = PhilConfig(models={role: f"openrouter:{role}/model" for role in LEGACY_SIX})
    propose_approaches(ctx_for(tmp_path, config, factory), GOAL, tree=tmp_path, overview="")
    assert factory.built == [("design", "openrouter:architect/model")]
```

In `tests/agents/test_registry.py`:
- add `"design"` to the expected `SPECS` names;
- add `"designer"` to the roles set: `{"classifier", "answerer", "designer"}`;
- change `shell_users == writers | {"answer"}` to `writers | {"answer", "design"}`;
- change the read-only set to `{"answer", "design"}`;
- change the light-harness set to `{"answer", "quick_implementer", "design"}`, and rename that test to `test_only_the_answerer_designer_and_quick_implementer_use_the_light_harness`.

In `tests/chat/test_intake_spec.py`, change `set(ROLES) - {"classifier", "answerer"}` to `set(ROLES) - {"classifier", "answerer", "designer"}`, and update that test's name and comment to mention the designer falling back to the architect's model.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/chat/test_design.py tests/agents/test_registry.py tests/chat/test_intake_spec.py -q -n 0`
Expected: FAIL with `ImportError` (`DESIGN_MAX_MODEL_CALLS`, `phil.chat.design`).

- [ ] **Step 3: Implement**

In `src/phil/config.py`:
- `ROLES`: append `"designer"`.
- `DEFAULT_TIERS`: add `"designer": "high",`.
- `DEFAULT_BUDGETS`: add `"designer": 24_000`.
- Extend the `CHAT_ROLES` comment with: "Nor is the designer: without a model of its own it uses the architect's."

In `src/phil/cli/main.py`, change `CHAT_ROLES + ("answerer",) + RUN_ROLES` to `CHAT_ROLES + ("answerer", "designer") + RUN_ROLES`, and update the comment to say "The answerer's and designer's keys are checked when they have models of their own".

In `src/phil/agents/spec.py`, after `FINISH_NOW`:

```python
PROPOSE_NOW = "Your tool budget is used up. Propose your approaches now from what you have read."
```

In `src/phil/agents/registry.py`:
- import `PROPOSE_NOW`, and `Approaches` and `DesignInput` from `phil.contracts`;
- add `DESIGN_MAX_MODEL_CALLS = 8  # the designer's model-call budget, enforced like the answerer's`;
- add this `SPECS` entry:

```python
    "design": AgentSpec(
        "design", "designer", DesignInput, Approaches, tools=("shell",), harness="light", shared_prompt=False,
        read_only_shell=True, max_model_calls=DESIGN_MAX_MODEL_CALLS, cap_message=PROPOSE_NOW,
    ),
```

Create `src/phil/prompts/design.md`:

```markdown
# Role: Designer

The user wants a change whose approach is still open. Before anyone plans it, propose 2 or 3 genuinely different ways to build it, so the user can pick one. You never change anything.

## Tools
- `ls`, `read_file`, `glob`, `grep` on the repository, and a shell that runs only read-only commands (`git log`, `grep`, `cat`, ...).
- Read only what you need to ground the approaches in this repo: start from `repo_overview`, then look at the files the goal touches.

## Approaches
- `options`: 2 or 3 approaches that differ in a way the user would care about (layout, structure, library, scope), not minor variations. Each has a short `name`, a `summary` of what gets built and where (in the repo's own terms), and 1–3 `tradeoffs`.
- `recommended`: the index (from 0) of the approach you'd pick for this repo. `reason`: one sentence on why.
- Respect the goal's `constraints` and `non_goals`.
- Don't write a plan or tasks; the architect does that once the user picks.
- Never invent files or conventions. If the repo doesn't show something, say so in a trade-off.
```

Create `src/phil/chat/design.py`:

```python
from dataclasses import replace
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts import Approaches, DesignInput, Goal
from phil.packets import build_packet


def propose_approaches(ctx: AgentContext, goal: Goal, *, tree: Path, overview: str, call: int = 1) -> Approaches:
    """2–3 approaches to `goal` from the read-only designer, which reads `tree` (the base commit's
    snapshot, as the architect does). Without a designer model it uses the architect's."""
    contract = DesignInput(goal=goal, repo_overview=overview)
    packet = build_packet("designer", contract, budget_tokens=ctx.config.budget_for("designer").max_input_tokens)
    # A legacy per-role config has no high tier: the designer then designs on the architect's model.
    model = ctx.config.model_for("architect") if ctx.config.missing_models(("designer",)) else None
    out = invoke_agent(get_spec("design"), packet, replace(ctx, workdir=tree), node="design", call=call, model=model)
    assert isinstance(out, Approaches)
    return out
```

In `src/phil/store/telemetry.py`, change `GOAL_NODES = ("route", "intake", "architect", "critic")` to include `"design"`, and update the docstring that lists the nodes. If a telemetry or `/show` test pins the tuple, update it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/chat/test_design.py tests/agents tests/chat/test_intake_spec.py tests/test_config.py tests/cli tests/ui/test_show_view.py tests/store -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`
Expected: all pass.

Commit message: `Add a read-only designer agent that proposes approaches`, plus the trailer.

---

### Task 4: The design step in the chat

**Files:**
- Modify: `src/phil/routing/policy.py:9-30`. Add `/full!` and `skips_design`.
- Modify: `src/phil/chat/planning.py`. `Planner` passes `approach` and `approach_note` to the architect.
- Modify: `src/phil/prompts/architect.md`. Add a `## Chosen approach` section before `## Revisions`.
- Modify: `src/phil/chat/controller.py`:
  - constants: `HELP`, `PROMPTS`, `TRANSCRIPT_STAGES`, `GOAL_JOB_STAGES` and `OVERRIDE_USAGE`, plus the new ones;
  - `__init__`, `_reset_goal`, `_prompt`, `_input`, `_interrupt`, `_forced_input`, `_begin_goal`, `_confirm_replace`, `_status_line`, `_build`, `_plan` and `_edit`;
  - `_save` and `_reopen` counters;
  - new `_wants_design`, `_design_job`, `_on_design_ready`, `_on_design_failed`, `_choose_approach` and `_chose`.
- Test: `tests/chat/test_design_flow.py` (new), `tests/chat/test_planning.py`, `tests/routing/` (the policy tests file; find it with `grep -rln parse_override tests`)

**Interfaces:**
- Consumes:
  - `propose_approaches(ctx, goal, *, tree, overview, call)` (Task 3);
  - `Approach`, `Approaches` and `ArchitectInput.chosen_approach` / `approach_note` (Task 1).
- Produces:
  - `Planner.draft(..., approach: Approach | None = None, approach_note: str = "")`
  - `Planner.revise(..., approach: Approach | None = None, approach_note: str = "")`
  - `phil.routing.policy.skips_design(text: str) -> bool`
  - chat stages `"designing"` (a goal-job stage) and `"choose_approach"`
  - transcript contract `approaches`, and notes `approach_chosen {name, note}` and `design_failed {error}`

- [ ] **Step 1: Write the failing tests**

Create `tests/chat/test_design_flow.py`:

```python
"""The design step before full planning (spec §3.4)."""

from phil.contracts import Approach, Approaches
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
from tests.chat.test_quick_flow import detectable, notes, quick_goal
from tests.chat.test_routing_flow import payloads, peek, route

APPROACHES = Approaches(
    options=[
        Approach(name="Footer band", summary="A full-width band above the footer.", tradeoffs=["Visible on every page"]),
        Approach(name="Inline card", summary="A card after the hero.", tradeoffs=["Only on the home page"]),
    ],
    recommended=1,
    reason="The home page is where visitors decide.",
)
OPEN = goal("Add a CTA section to the home page", approach_open=True)
PLANNING = {"architect": [plan()], "critic": [critique()]}


def test_an_open_approach_proposes_designs_and_the_pick_reaches_the_architect(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "2", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "1. Inline card (recommended)" in text and "2. Footer band" in text
    assert "Visible on every page" in text and "3. Describe your own" in text
    assert "Pick an approach [Enter = 1 / number / describe your own] › " in prompts
    [architect] = payloads(factory, "architect")
    assert "A full-width band above the footer." in architect and "A card after the hero." not in architect
    [chosen] = notes(calc_repo, "approach_chosen")
    assert chosen["name"] == "Footer band" and chosen["note"] == ""
    assert text.count("Goal: Add a CTA section to the home page") == 1  # not shown again before planning


def test_enter_picks_the_recommended_approach(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "A card after the hero." in payloads(factory, "architect")[0]


def test_typed_text_is_the_users_own_approach(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "a sticky banner at the top", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    [architect] = payloads(factory, "architect")
    assert "a sticky banner at the top" in architect
    assert "Footer band" not in architect and "Inline card" not in architect
    [chosen] = notes(calc_repo, "approach_chosen")
    assert chosen["name"] is None and chosen["note"] == "a sticky banner at the top"


def test_describe_your_own_by_number_asks_for_it(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "3", "a sticky banner", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "Describe your approach › " in prompts
    assert "a sticky banner" in payloads(factory, "architect")[0]


def test_an_out_of_range_number_asks_again(calc_repo):
    text, *_ = run_chat(
        calc_repo, ["add a CTA section", "7", "1", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "Pick 1–3, or describe your own approach." in text


def test_an_edit_keeps_the_chosen_approach(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "2", "edit", "make it two tasks", "n"],
        {"intake": [OPEN], "design": [APPROACHES], "architect": [plan(), plan(n=2)], "critic": [critique(), critique()]},
    )
    first, second = payloads(factory, "architect")
    assert "A full-width band above the footer." in first and "A full-width band above the footer." in second


def test_a_designer_failure_plans_directly(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", "n"], {"intake": [OPEN], "design": [RuntimeError("down")] * 3, **PLANNING}
    )
    assert "Couldn't propose designs; planning directly." in text
    assert "Plan CALC v1" in text
    assert notes(calc_repo, "design_failed")


def test_full_bang_skips_the_designer_in_any_case(calc_repo):
    for prefix in ("/full!", "/FULL!"):
        text, spawned, runs, factory, prompts = run_chat(
            calc_repo, [f"{prefix} add a CTA section", "n"], {"intake": [OPEN], **PLANNING}
        )
        assert "Forced: full path, no design proposals" in text
        assert "Approaches" not in text and factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_plain_full_still_proposes_designs(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["/full add a CTA section", "1", "n"], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "1. Inline card (recommended)" in text


def test_a_clear_request_skips_the_designer(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(calc_repo, ["add subtract", "n"], {"intake": [goal()], **PLANNING})
    assert "Approaches" not in text and factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_open_questions_left_after_go_skip_the_designer(calc_repo):
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add a CTA section", "go", "n"],
        {"intake": [goal("Add a CTA section to the home page", approach_open=True, open_questions=["Which page?"])], **PLANNING},
    )
    assert "Approaches" not in text and factory.remaining() == {"intake": 0, "architect": 0, "critic": 0}


def test_the_quick_path_never_designs(calc_repo):
    detectable(calc_repo)
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["fix the typo in calc"], {"route": [route("simple_change")], "intake": [quick_goal(approach_open=True)]}
    )
    assert "Quick change:" in text and "Approaches" not in text and len(spawned) == 1


def test_ctrl_c_at_the_choice_cancels_the_goal(calc_repo):
    seen = {}

    def ctrl_c(controller):
        raise KeyboardInterrupt

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo, ["add a CTA section", ctrl_c, peek(seen)], {"intake": [OPEN], "design": [APPROACHES], **PLANNING}
    )
    assert "Cancelled the current goal." in text and seen["stage"] == "idle"
    assert payloads(factory, "architect") == []
```

Append to `tests/chat/test_planning.py`. Follow the file's existing `Planner` test setup; if it has a fixture building a `Planner` with a scripted factory, reuse it. The shape is:

```python
def test_the_chosen_approach_and_note_reach_the_architect(chat_ctx, tmp_path):
    from phil.contracts import Approach

    chosen = Approach(name="Footer band", summary="A band above the footer.")
    # build a Planner whose factory scripts architect [plan()] and critic [critique()], as the file's other Planner tests do
    draft = planner.draft(goal(), tmp_path, approach=chosen, approach_note="keep it small")
    payload = str(factory.calls[0][1])
    assert "A band above the footer." in payload and "keep it small" in payload
```

Write it concretely against the file's real fixtures (read `tests/chat/test_planning.py` first). It must assert that both values appear in the architect's payload.

In the routing policy tests, add:

```python
def test_full_bang_forces_full_and_skips_design():
    from phil.routing.policy import parse_override, skips_design

    assert parse_override("/full! add a CTA") == ("full", "add a CTA")
    assert parse_override("/FULL! add a CTA") == ("full", "add a CTA")
    assert skips_design("/Full! add a CTA") and not skips_design("/full add a CTA") and not skips_design("add a CTA")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/chat/test_design_flow.py tests/chat/test_planning.py -q -n 0`, plus the routing policy test file.
Expected: FAIL. No design step runs, and `skips_design` and the `approach` keyword don't exist.

- [ ] **Step 3: Routing policy**

In `src/phil/routing/policy.py`, change `OVERRIDES` and add the helper:

```python
OVERRIDES = {"/ask": "answer", "/quick": "quick", "/full": "full", "/full!": "full"}
NO_DESIGN = "/full!"  # the full path without the designer's proposals


def skips_design(text: str) -> bool:
    """True when the message starts with `/full!` (any case): plan fully, without design proposals."""
    parts = text.strip().split(maxsplit=1)
    return bool(parts) and parts[0].lower() == NO_DESIGN
```

If `phil.routing`'s `__init__` re-exports policy names, export `skips_design` too.

- [ ] **Step 4: Planner and architect prompt**

In `src/phil/chat/planning.py`, import `Approach` from `phil.contracts`. Then thread `approach: Approach | None = None, approach_note: str = ""` through each method:
- `_architect`: as its last two parameters, set into `ArchitectInput(..., chosen_approach=approach, approach_note=approach_note)`;
- `_cycle`: as its last two parameters, passed to both `_architect` calls;
- `draft` and `revise`: as keyword-only parameters after `prior_attempt`, passed to `_cycle`.

Positional order in `_cycle(goal, previous, critique, tree, on_step, ctx, prior_attempt, approach, approach_note)` and in `_architect(ctx, goal, previous, critique, tree, call, prior_attempt, approach, approach_note)`.

In `src/phil/prompts/architect.md`, insert before `## Revisions`:

```markdown
## Chosen approach
- `chosen_approach`: when set, the user picked this approach from the designer's proposals. Plan it; don't reopen the choice.
- `approach_note`: when set, it is the user's own description of how to build it. Plan that.
```

- [ ] **Step 5: The controller**

In `src/phil/chat/controller.py`:

1. **Imports:**
   - `from phil.chat.design import propose_approaches`;
   - add `Approach` and `Approaches` to the `phil.contracts` import;
   - `skips_design`, from wherever `parse_override` is imported.

2. **Constants:**

```python
HELP = (
    "Type a goal or a question. Prefix with /ask, /quick or /full to choose the path "
    "(/full! plans fully without design proposals). "
    "Commands: /runs, /btw <question> (ask while work continues), "
    "/answer (a paused run's question), /resume (a failed or stopped run), /show (the chat's run: usage "
    "and numbered details), /more <n> (print detail n), /park <note> (set an idea aside), /help, "
    "/quit (or Ctrl-D)."
)
```

   - Add to `PROMPTS`: `"choose_approach": "Pick an approach [Enter = 1 / number / describe your own] › ",`
   - Add the new constants:

```python
DESCRIBE_OWN = "Describe your own"
DESCRIBE_PROMPT = "Describe your approach › "
DESIGN_FALLBACK = "Couldn't propose designs; planning directly."
```

   - `TRANSCRIPT_STAGES`: add `"designing": "goal", "choose_approach": "approach"`.
   - `GOAL_JOB_STAGES = ("routing", "intake", "planning", "answering", "designing")`
   - `OVERRIDE_USAGE = "Usage: /ask|/quick|/full|/full! <message>"`

3. **`__init__`:**

```python
        self._design_calls = 0
        self._skip_design = False  # the goal was typed with /full!
        self._replacement_skip = False  # a replacement typed with /full!
        self._approach_order: list[Approach] = []  # the designer's approaches, recommended first
        self._approach: Approach | None = None  # the user's pick, planned by the architect
        self._approach_note = ""  # or their own description
        self._describing = False  # "Describe your own" was picked: the next input is the description
```

4. **`_reset_goal`:**

```python
        self._skip_design, self._approach_order, self._approach, self._approach_note = False, [], None, ""
        self._describing = False
```

5. **`_prompt`:** before the final return,

```python
        if self.stage == "choose_approach" and self._describing:
            return DESCRIBE_PROMPT
```

6. **`_input`:**
   - add `elif stage == "choose_approach": self._choose_approach(text)` after the `questions` branch;
   - in the `GOAL_JOB_STAGES` replacement branch, also set `self._replacement_skip = False`.

7. **`_interrupt`:**
   - the cancel branch becomes `elif self.stage in (*GOAL_JOB_STAGES, "questions", "choose_approach"):`;
   - the `confirm_replace` branch also clears `self._replacement_skip = False`.

8. **`_forced_input`:**
   - compute `skip = skips_design(raw)`;
   - pass `skip_design=skip` to both `_begin_goal(rest, forced=forced, ...)` calls;
   - in the replacement branch set `self._replacement_skip = skip`.

9. **`_begin_goal`:**
   - add a keyword parameter `skip_design: bool = False`;
   - set `self._skip_design = skip_design` right after `self._reset_goal()`.

10. **`_confirm_replace`:**
    - call `self._begin_goal(self._replacement, forced=self._replacement_forced, skip_design=self._replacement_skip)`;
    - clear `self._replacement_skip = False` with the others.

11. **`_status_line`:** the forced line becomes

```python
        if route.source == "forced":
            return f"Forced: {route.depth} path" + (", no design proposals" if self._skip_design else "")
```

12. **`_build`:**

```python
    def _build(self, goal: Goal) -> None:
        """Take the goal to a run: the quick path when the route (or, if it deferred, intake) chose
        quick; else the design step when the approach is open; else full planning."""
        route = self._route
        if route is not None and (route.depth == "quick" or (route.depth is None and goal.depth == "quick")):
            self._quick(goal)
        elif self._wants_design(goal):
            self._design_job(goal)
        else:
            self._plan(goal)

    def _wants_design(self, goal: Goal) -> bool:
        """The designer runs when the approach is open, nothing is left to ask, and the message
        didn't start with /full! (spec §3.4)."""
        return goal.approach_open and not goal.open_questions and not self._skip_design
```

13. **The design step:**

```python
    def _design_job(self, goal: Goal) -> None:
        render_goal(self.console, goal)
        self.console.print("[phil.muted]Proposing approaches…[/]")
        self._set_stage("designing")
        self._design_calls += 1
        call, generation, overview = self._design_calls, self._generation, self.overview

        def fn(ctx: AgentContext) -> dict:
            self._step("snapshot", generation)
            tree = self._snapshot(generation)
            self._step("designing", generation)
            return {"approaches": propose_approaches(ctx, goal, tree=tree, overview=overview, call=call)}

        self._job("design_ready", fn, failed="design_failed")

    def _on_design_ready(self, data: dict) -> None:
        approaches: Approaches = data["approaches"]
        self.session.contract("approaches", approaches)
        rec = approaches.recommended
        self._approach_order = [approaches.options[rec], *(a for i, a in enumerate(approaches.options) if i != rec)]
        self.console.print("[phil.brand]Approaches:[/]")
        for n, approach in enumerate(self._approach_order, 1):
            mark = " (recommended)" if n == 1 else ""
            self.console.print(f"[phil.agent]{n}. {escape(_clip(approach.name))}{mark}[/]")
            self.console.print(f"   {escape(_clip(approach.summary))}")
            for tradeoff in approach.tradeoffs:
                self.console.print(f"   [phil.muted]– {escape(_clip(tradeoff))}[/]")
        if approaches.reason:
            self.console.print(f"[phil.muted]{escape(_clip(approaches.reason))}[/]")
        self.console.print(f"{len(self._approach_order) + 1}. {DESCRIBE_OWN}")
        self._set_stage("choose_approach")

    def _on_design_failed(self, data: dict) -> None:
        self._safe_note("design_failed", error=data["error"])
        self.console.print(f"[phil.muted]{DESIGN_FALLBACK}[/]")
        self._plan(self._goal, show_goal=False)

    def _choose_approach(self, text: str) -> None:
        """Enter or a number picks a proposed approach; the last number asks for a description; any
        other text is the user's own approach. Then the architect plans it."""
        order = self._approach_order
        if self._describing:
            if text:
                self._describing = False
                self._chose(None, text)
            return
        if not text:
            self._chose(order[0], "")
        elif text.rstrip(".)").isdigit():
            choice = int(text.rstrip(".)"))
            if 1 <= choice <= len(order):
                self._chose(order[choice - 1], "")
            elif choice == len(order) + 1:
                self._describing = True
            else:
                self.console.print(f"Pick 1–{len(order) + 1}, or describe your own approach.")
        else:
            self._chose(None, text)

    def _chose(self, approach: Approach | None, note: str) -> None:
        self._approach, self._approach_note = approach, note
        self._safe_note("approach_chosen", name=approach.name if approach else None, note=note)
        self._recent.append(f"you: {(approach.name if approach else note)[:TURN_CHARS]}")
        self._plan(self._goal, show_goal=False)
```

14. **`_plan`:**
    - add the keyword parameter `show_goal: bool = True`;
    - call `render_goal(...)` only when `show_goal`;
    - capture `approach, note = self._approach, self._approach_note` before `fn`;
    - pass `approach=approach, approach_note=note` to `self.planner.draft(...)`.

15. **`_edit`:** capture `approach, note = self._approach, self._approach_note`, and pass them to `self.planner.revise(...)` as `approach=approach, approach_note=note`.

16. **`_save` / `_reopen`:**
    - add `"design": self._design_calls` to the saved counters;
    - in `_reopen`, add `"design"` to the counter keys;
    - add `self._design_calls = max(self._design_calls, values["design"])`.

    A chat reopened at `designing` or `choose_approach` takes the existing "closed before a plan was ready" path. Leave it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/chat tests/routing tests/agents -q -n 0`
Expected: PASS.
- If an existing test pins `HELP`, `OVERRIDE_USAGE` or `GOAL_JOB_STAGES`, update it to the new value.
- If `test_a_designer_failure_plans_directly` sees an exhausted-script error instead of the `RuntimeError`, that still means the designer failed. Keep the assertions as written; they hold either way.

- [ ] **Step 7: Run the full suite, then commit**

Run: `uv run pytest -q`
Expected: all pass.

Commit message: `Propose approaches before planning when the approach is open`, plus the trailer.
