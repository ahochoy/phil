# Record and Salvage Rejected Answers: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A rejected agent answer records the agent's final messages, and an almost-right answer is salvaged before Phil declares a `ContractViolation`.

**Architecture:** A new `agents/salvage.py` holds two pure functions, one that summarises the last messages and one that recovers a candidate answer. `invoke_agent` calls them at the single point where it finds no `structured_response`.

**Tech Stack:** Python 3.12, LangChain messages, pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-phil-answer-salvage-design.md`

## Global Constraints

- No new dependencies. Salvage makes no model calls.
- `LAST_MESSAGES = 6`. The text and args cut is `RAW_TEXT_LIMIT = 2000`, which already exists in `invoke.py`. Image parts are written as `[image]`.
- The answer tool name is `spec.out_contract.__name__`.
- New telemetry outcome: `"salvaged"`.
- Behaviour is unchanged when `structured_response` is present.
- Edit only with Edit/Write. Commit with `git commit -F .git/phil-commit-msg.txt`, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Tests run as `PATH="$PWD/.venv/bin:$PATH" PHIL_HOME=$(mktemp -d) .venv/bin/pytest -q <paths>`.

## Review Focus

1. **Truncated answer.** The answer tool call is cut off mid-JSON (`finish_reason: "length"`). It is repaired and salvaged if it validates; otherwise the record shows `finish_reason: "length"`.
2. **JSON in plain text with prose around it.** The JSON inside a `{…}` span holds braces in strings. It is extracted correctly.
3. **Validation fails on every candidate.** Behaviour matches today. The problems gain one `salvage: …` line, and the retry still happens.
4. **A message whose content is a list with image parts.** The record shows `[image]` and no base64.
5. **The model never called the answer tool and wrote no JSON.** There is no salvage, and the record shows its last messages.

---

### Task 1: Summarise the last messages, and salvage a candidate

**Files:**
- Create: `src/phil/agents/salvage.py`
- Test: `tests/agents/test_salvage.py`

**Interfaces:**
- Produces:
  - `LAST_MESSAGES = 6`
  - `summarise(messages: list, limit: int = 2000) -> list[dict]`
  - `extract_json(text: str) -> str | None`
  - `@dataclass(frozen=True) class Salvaged: data: dict; route: str  # "invalid_call" | "answer_call" | "text"`
  - `salvage(messages: list, tool_name: str, validate: Callable[[dict], list[str]]) -> tuple[Salvaged | None, list[str]]`. The second item holds the problems from the last candidate tried, each prefixed `salvage: `.

- [ ] **Step 1: Write the failing tests** using `AIMessage`, `ToolMessage` and `HumanMessage`:
  - `test_summarise_keeps_the_last_six_oldest_first_with_calls_and_finish_reason`.
  - `test_summarise_writes_images_as_a_placeholder` (Review Focus 4).
  - `test_extract_json_from_a_fence_and_from_braces_with_braces_in_strings` (Review Focus 2). Example: `'Here: {"a": "x}y", "b": {"c": 1}} done'` → `'{"a": "x}y", "b": {"c": 1}}'`.
  - `test_salvage_repairs_a_truncated_invalid_call` (Review Focus 1). The input is `invalid_tool_calls=[{"name": "Plan", "args": '{"summary": "s", "tasks": [', ...}]` plus a validate that accepts `{"summary": "s", "tasks": []}`. The result has route `invalid_call`.
  - `test_salvage_revalidates_an_answer_call`.
  - `test_salvage_reads_json_from_text`.
  - `test_salvage_reports_the_last_problems_when_nothing_validates` (Review Focus 3). It returns `(None, ["salvage: tasks: Field required"])`.
  - `test_salvage_returns_nothing_without_candidates` (Review Focus 5). It returns `(None, [])`.
- [ ] **Step 2: Run them.** Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement.**
  - Use `tool_args.repair_arguments` for the repair.
  - `validate` returns a problems list, where empty means valid. Keep the module free of `invoke` imports.
  - Walk at most `LAST_MESSAGES` AI messages, newest first.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** with the message "Agents: summarise and salvage a rejected answer".

### Task 2: Wire record and salvage into `invoke_agent`

**Files:**
- Modify:
  - `src/phil/agents/invoke.py`, around lines 517-565. Change the flow so that when `parse_problems is None` and `result.get("structured_response") is None`, it calls `salvage(messages, spec.out_contract.__name__, validate)`, where `validate` wraps `_validate(spec, data)[1]`.
    - When a salvaged result validates, use it as `output`, set `outcome = "salvaged"`, add a debug log naming the route, and let the evidence checks run as usual.
    - Otherwise `problems = ["no structured output was returned", *salvage_problems]`.
    - The rejected artifact gains `last_messages` (`summarise(...)`), `model_calls` (the AI message count) and `max_model_calls` (`spec.max_model_calls`).
  - `src/phil/store/telemetry.py`: add `"salvaged"` to the `outcome` Literal. Check that every reader of `outcome` (grep `outcome ==` and `"invalid"` in src) treats `salvaged` as a success where it counts successes.
- Test: `tests/agents/test_invoke.py`.

**Interfaces:**
- Consumes `summarise`, `salvage` and `LAST_MESSAGES` (Task 1).

- [ ] **Step 1: Write the failing tests** with the fake agent factory, which returns scripted messages and no structured response:
  - `test_a_broken_answer_call_is_salvaged`. `invoke_agent` returns the contract, and the telemetry row's outcome is `salvaged`.
  - `test_a_rejected_answer_records_last_messages`. Read the `.rejected.json` and assert `last_messages[-1]["role"] == "ai"`, plus `finish_reason`, `model_calls` and `max_model_calls`.
  - `test_failed_salvage_adds_its_problem_to_the_retry`. The retry message includes the `salvage: …` line.
  - `test_a_structured_response_skips_salvage`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** as listed under Files.
- [ ] **Step 4: Run** `tests/agents tests/store`, then the full suite. Expected: PASS.
- [ ] **Step 5: Commit** with the message "Agents: rejected answers record the agent's last messages; near misses are salvaged".

### Task 3 (user): live check

Install with `uv tool install --editable . --force`. In `hochoy-biz`, with everything still on `ling-3.0-flash`, ask for a change that needs the architect.

Either the plan now arrives, and telemetry shows `salvaged`, or it fails again. If it fails, open `/more 1` and the `outputs/architect-run-N.rejected.json` it names, and read `last_messages` and `finish_reason`. Those tell us whether option 3 (prose then convert) is needed.
