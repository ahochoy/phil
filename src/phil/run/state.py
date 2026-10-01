import re
from typing import Any, Literal, TypedDict

from phil.contracts import Issue, Plan, Task
from phil.store.telemetry import Totals, UsageLine, format_cost


class RunState(TypedDict, total=False):
    run_id: str
    plan: dict[str, Any]
    base_sha: str
    test_cmd: str
    status: str
    baseline_failures: list[str]
    initial_baseline: list[str]
    base_passed: int | None
    base_skipped: int | None
    task_index: int
    task_base_sha: str
    phase: str
    red_tree: str
    attempts: int
    call_seq: int
    last_problems: list[str]
    last_report: dict[str, Any] | None
    red_snapshot: dict[str, str]
    hint: str | None
    implement_failed: bool
    denied: list[str]
    approved: list[str]
    escalation: dict[str, Any] | None
    verdict: str
    next: str
    tester_done: bool
    review_rounds: int
    budget_limit_tokens: int | None
    budget_limit_cost: float | None
    budget_warned: bool
    original_task_ids: list[str]
    open_issues: list[dict[str, Any]]
    commit_bypass: bool
    worklogs: dict[str, dict]
    keep_worktree: bool
    rebaseline: bool
    config_test_cmd: str | None
    depth: str
    patching: bool
    patched_issues: list[dict[str, Any]]
    moved_to_full: bool


def initial_state(
    run_id: str, plan: Plan, base_sha: str, test_cmd: str, config_test_cmd: str | None = None, depth: str = "full"
) -> RunState:
    """`config_test_cmd` is phil.toml's [project] test_cmd at launch: a resume switches the run's
    command only when phil.toml has changed since. A `depth="quick"` run starts with its tester
    step already done, skipping it."""
    return RunState(
        run_id=run_id,
        plan=plan.model_dump(),
        base_sha=base_sha,
        test_cmd=test_cmd,
        status="pending",
        baseline_failures=[],
        initial_baseline=[],
        base_passed=None,
        base_skipped=None,
        task_index=-1,
        task_base_sha=base_sha,
        phase="red",
        red_tree="",
        attempts=0,
        call_seq=0,
        last_problems=[],
        last_report=None,
        red_snapshot={},
        hint=None,
        implement_failed=False,
        denied=[],
        approved=[],
        escalation=None,
        verdict="",
        next="",
        tester_done=(depth == "quick"),
        review_rounds=0,
        budget_limit_tokens=None,
        budget_limit_cost=None,
        budget_warned=False,
        original_task_ids=[task.id for task in plan.tasks],
        open_issues=[],
        commit_bypass=False,
        worklogs={},
        keep_worktree=False,
        rebaseline=False,
        config_test_cmd=config_test_cmd,
        depth=depth,
        patching=False,
        patched_issues=[],
    )


def run_depth(state: RunState) -> str:
    """The run's depth: `state["depth"]` if set, else "full" (a checkpoint from before M3b)."""
    return state.get("depth") or "full"


def load_plan(state: RunState) -> Plan:
    return Plan.model_validate(state["plan"])


def next_todo(plan: Plan) -> int | None:
    for index, task in enumerate(plan.tasks):
        if task.status == "TODO":
            return index
    return None


def with_task_status(plan: Plan, index: int, status: str) -> Plan:
    tasks = list(plan.tasks)
    tasks[index] = tasks[index].model_copy(update={"status": status})
    return plan.model_copy(update={"tasks": tasks})


def _finding_check_cmd(plan: Plan, test_cmd: str | None) -> str | None:
    """The check_cmd a finding's fix task borrows, or None to keep it tdd.

    A tdd task can never pass red in a run with no test command, or in an all-check run (whose
    tests aren't what the plan verifies with), so such a run's findings become check tasks using
    the plan's first check_cmd. Findings only ever become check tasks in an all-check run, so
    "every task is check" is the same as "every original task is check". With no check_cmd to
    borrow the task stays tdd, and the run escalates for a test command as before."""
    first_check = next((task.check_cmd for task in plan.tasks if task.check_cmd), None)
    if not test_cmd or all(task.verify == "check" for task in plan.tasks):
        return first_check
    return None


def issues_to_tasks(plan: Plan, issues: list[Issue], source: str, test_cmd: str | None) -> Plan:
    number = max(int(task.id.rsplit("-", 1)[1]) for task in plan.tasks)
    check_cmd = _finding_check_cmd(plan, test_cmd)
    verify: Literal["tdd", "check"] = "check" if check_cmd else "tdd"
    new_tasks: list[Task] = []
    for issue in issues:
        number += 1
        note = issue.note.strip()
        new_tasks.append(
            Task(
                id=f"{plan.keyword}-{number:03d}",
                description=f"Fix ({source}): {note}",
                acceptance_criteria=[note],
                files_hint=[issue.file] if issue.file else [],
                verify=verify,
                check_cmd=check_cmd,
            )
        )
    return plan.model_copy(update={"tasks": [*plan.tasks, *new_tasks]})


_LEADING_HEADING_RE = re.compile(r"^#+\s*")
_LEADING_LIST_RE = re.compile(r"^(?:[-*+]|\d+\.)\s+")
# The underscore forms (`__bold__`/`_italic_`) are only stripped where they aren't part of a
# word (CommonMark's intraword-emphasis rule for underscores), so a plain identifier like
# `test_add_strings` or `__init__` survives untouched.
_BOLD_UNDERSCORE_RE = re.compile(r"(?<!\w)__(\S(?:.*?\S)?)__(?!\w)")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!\w)_(\S(?:.*?\S)?)_(?!\w)")
# The star forms (`**bold**`/`*italic*`) need a CommonMark-style flanking check too, or plain
# arithmetic gets mangled (`3 * 4 * 5`, `x**2 and y**2 differ`): an opening `*`/`**` must not be
# preceded by a word character and must be followed by a non-space; a closing one must be
# preceded by a non-space and must not be followed by a word character. `(?<!\*)`/`(?!\*)` on
# the single-star form additionally keep it from firing on one half of a `**` pair.
_BOLD_STAR_RE = re.compile(r"(?<!\w)\*\*(?!\s)(.+?)(?<!\s)\*\*(?!\w)")
_ITALIC_STAR_RE = re.compile(r"(?<!\*)(?<!\w)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)(?!\w)")
_SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2}


def clean_note(text: str, *, limit: int = 200) -> str:
    """Render a note as one plain-text line: newlines/whitespace collapsed, markdown emphasis,
    backticks and leading `#`/list markers stripped, capped to `limit` chars with an `…`."""
    collapsed = " ".join(text.split())
    collapsed = _LEADING_HEADING_RE.sub("", collapsed)
    collapsed = _LEADING_LIST_RE.sub("", collapsed)
    collapsed = _BOLD_STAR_RE.sub(r"\1", collapsed)
    collapsed = _BOLD_UNDERSCORE_RE.sub(r"\1", collapsed)
    collapsed = _ITALIC_STAR_RE.sub(r"\1", collapsed)
    collapsed = _ITALIC_UNDERSCORE_RE.sub(r"\1", collapsed)
    collapsed = collapsed.replace("`", "").strip()
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by (task_id, cleaned note lower-cased), keeping the highest severity seen
    for that key and the note cleaned to one line; stable order of first appearance."""
    order: list[tuple[Any, str]] = []
    best: dict[tuple[Any, str], dict[str, Any]] = {}
    for issue in issues:
        cleaned_note = clean_note(issue["note"])
        key = (issue.get("task_id"), cleaned_note.lower())
        candidate = {**issue, "note": cleaned_note}
        current = best.get(key)
        if current is None:
            order.append(key)
            best[key] = candidate
        elif _SEVERITY_RANK[candidate["severity"]] < _SEVERITY_RANK[current["severity"]]:
            best[key] = candidate
    return [best[key] for key in order]


def issue_line(issue: dict[str, Any]) -> str:
    location = ""
    if issue.get("file"):
        location = f" [{issue['file']}" + (f":{issue['line']}" if issue.get("line") else "") + "]"
    return f"- ({issue['severity']}) {issue['note']}{location}"


def task_lines(plan: Plan) -> list[str]:
    lines = []
    for task in plan.tasks:
        mark = "x" if task.status == "DONE" else " "
        suffix = "" if task.status in ("DONE", "TODO") else f" ({task.status})"
        lines.append(f"- [{mark}] {task.id} {task.description}{suffix}")
    return lines


def _totals_suffix(source: str) -> str:
    if source == "estimated":
        return " (estimated)"
    if source == "unknown":
        return " (partly unknown)"
    return ""


def _usage_line(line: UsageLine) -> str:
    calls_word = "call" if line.calls == 1 else "calls"
    model_word = "model call" if line.model_calls == 1 else "model calls"
    text = (
        f"- {line.layer}/{line.role}: {line.calls} {calls_word} ({line.model_calls} {model_word}) · "
        f"{line.input_tokens:,} in / {line.output_tokens:,} out · {format_cost(line.cost_usd, line.cost_source)}"
    )
    if line.tool_calls:
        tools = ", ".join(f"{name}×{count}" for name, count in line.tool_calls.items())
        text += f" · tools: {tools}"
    if line.retries:
        text += f" · retries: {line.retries}"
    return text


def render_summary(
    *,
    run_id: str,
    plan: Plan,
    status: str,
    branch: str,
    base_sha: str,
    head_sha: str,
    open_issues: list[dict[str, Any]],
    usage: list[UsageLine] | None = None,
    totals: Totals | None = None,
) -> str:
    lines = [
        f"# Run {run_id} · {plan.keyword}",
        "",
        f"Status: {status} · branch {branch} · {base_sha[:8]}..{head_sha[:8]}",
        "",
        "## Tasks",
        *task_lines(plan),
        "",
        "## Open issues",
    ]
    lines += [issue_line(issue) for issue in dedupe_issues(open_issues)] or ["- (none)"]
    if totals is not None:
        lines += [
            "",
            "## Usage",
            f"Total: {totals.tokens:,} tokens · {format_cost(totals.cost_usd, totals.cost_source)}"
            f"{_totals_suffix(totals.cost_source)}",
        ]
        lines += [_usage_line(line) for line in usage or []]
    return "\n".join(lines) + "\n"
