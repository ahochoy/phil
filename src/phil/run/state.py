from typing import Any, TypedDict

from phil.contracts import Issue, Plan, Task


class RunState(TypedDict, total=False):
    run_id: str
    plan: dict[str, Any]
    base_sha: str
    test_cmd: str
    status: str
    baseline_failures: list[str]
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
    budget_override: bool
    original_task_ids: list[str]
    open_issues: list[dict[str, Any]]


def initial_state(run_id: str, plan: Plan, base_sha: str, test_cmd: str) -> RunState:
    return RunState(
        run_id=run_id,
        plan=plan.model_dump(),
        base_sha=base_sha,
        test_cmd=test_cmd,
        status="pending",
        baseline_failures=[],
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
        tester_done=False,
        review_rounds=0,
        budget_override=False,
        original_task_ids=[task.id for task in plan.tasks],
        open_issues=[],
    )


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


def issues_to_tasks(plan: Plan, issues: list[Issue], source: str) -> Plan:
    number = max(int(task.id.rsplit("-", 1)[1]) for task in plan.tasks)
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
            )
        )
    return plan.model_copy(update={"tasks": [*plan.tasks, *new_tasks]})


def _issue_line(issue: dict[str, Any]) -> str:
    location = ""
    if issue.get("file"):
        location = f" [{issue['file']}" + (f":{issue['line']}" if issue.get("line") else "") + "]"
    return f"- ({issue['severity']}) {issue['note']}{location}"


def render_summary(
    *,
    run_id: str,
    plan: Plan,
    status: str,
    branch: str,
    base_sha: str,
    head_sha: str,
    open_issues: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Run {run_id} · {plan.keyword}",
        "",
        f"Status: {status} · branch {branch} · {base_sha[:8]}..{head_sha[:8]}",
        "",
        "## Tasks",
    ]
    for task in plan.tasks:
        mark = "x" if task.status == "DONE" else " "
        suffix = "" if task.status in ("DONE", "TODO") else f" ({task.status})"
        lines.append(f"- [{mark}] {task.id} {task.description}{suffix}")
    lines += ["", "## Open issues"]
    lines += [_issue_line(issue) for issue in open_issues] or ["- (none)"]
    return "\n".join(lines) + "\n"
