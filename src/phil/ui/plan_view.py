from rich.console import Console
from rich.markup import escape

from phil.chat.planning import PlanDraft
from phil.contracts import Goal

MAX_CRITERIA = 3
MAX_NOTES = 5
MAX_QUESTIONS = 3
MAX_LINE = 160


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LINE else text[: MAX_LINE - 1] + "…"


def render_goal(console: Console, goal: Goal) -> None:
    console.print(f"[phil.brand]Goal:[/] {escape(_clip(goal.objective))}")
    for label, items in (("Constraints", goal.constraints), ("Not doing", goal.non_goals)):
        if items:
            console.print(f"[phil.muted]{label}:[/]")
            for item in items:
                console.print(f"  • {escape(_clip(item))}")
    if goal.open_questions:
        console.print("[phil.muted]Open questions:[/]")
        for question in goal.open_questions[:MAX_QUESTIONS]:
            console.print(f"  ? {escape(_clip(question))}")
        extra = len(goal.open_questions) - MAX_QUESTIONS
        if extra > 0:
            console.print(f"  [phil.muted](+{extra} more)[/]")


def render_plan(
    console: Console, draft: PlanDraft, *, test_cmd: str | None, test_cmd_note: str | None, git_note: str | None
) -> None:
    plan = draft.plan
    count = len(plan.tasks)
    console.print(
        f"[phil.brand]Plan {escape(plan.keyword)} v{draft.version} · {count} task{'s' if count != 1 else ''}[/]"
    )
    console.print(escape(_clip(plan.description)))
    for task in plan.tasks:
        console.print(f"  [phil.id]{escape(task.id)}[/]  {escape(_clip(task.description))}")
        for criterion in task.acceptance_criteria[:MAX_CRITERIA]:
            console.print(f"      [phil.gate.pass]✓[/] {escape(_clip(criterion))}")
        extra = len(task.acceptance_criteria) - MAX_CRITERIA
        if extra > 0:
            console.print(f"      [phil.muted](+{extra} more)[/]")
    console.print(f"[phil.muted]Critic ({escape(draft.critique.verdict)}):[/]")
    for note in plan.critic_notes[:MAX_NOTES]:
        console.print(f"  • {escape(_clip(note))}")
    extra = len(plan.critic_notes) - MAX_NOTES
    if extra > 0:
        console.print(f"  [phil.muted](+{extra} more)[/]")
    if test_cmd:
        console.print(f"Tests: {escape(test_cmd)}")  # shown in full: it's what the user approves
    else:
        console.print("[phil.warn]Tests: none — set test_cmd in the plan or phil.toml[/]")
    if test_cmd_note:
        console.print(f"[phil.warn]⚠ {escape(_clip(test_cmd_note))}[/]")
    if git_note:
        console.print(f"[phil.muted]{escape(git_note)}[/]")
