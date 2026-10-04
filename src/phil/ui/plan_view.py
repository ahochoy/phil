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


SOURCE_LABELS = {"plan": " (from the plan)", "config": " (from your config)", "detected": " (detected)"}


def render_plan(
    console: Console,
    draft: PlanDraft,
    *,
    test_cmd: str | None,
    test_cmd_note: str | None,
    git_note: str | None,
    test_cmd_source: str | None = None,
    test_cmd_origin: str | None = None,
    setup_cmd: str | None = None,
    setup_cmd_source: str | None = None,
) -> None:
    """`test_cmd_origin` names the file a "config" test command came from (`config.sources`):
    "phil.toml", the global config's path, or "--set". A `setup_cmd_source` of "unchecked" (nothing
    could be detected) prints `setup: none`."""
    plan = draft.plan
    count = len(plan.tasks)
    console.print(
        f"[phil.brand]Plan {escape(plan.keyword)} v{draft.version} · {count} task{'s' if count != 1 else ''}[/]"
    )
    console.print(escape(_clip(plan.description)))
    for task in plan.tasks:
        # A check command is shown in full: it's what the user approves.
        check = f"  (check: {escape(task.check_cmd)})" if task.verify == "check" and task.check_cmd else ""
        console.print(f"  [phil.id]{escape(task.id)}[/]  {escape(_clip(task.description))}{check}")
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
    if setup_cmd:
        suffix = " (detected)" if setup_cmd_source == "detected" else ""
        console.print(f"setup: {escape(setup_cmd)}{suffix}")
    elif setup_cmd_source == "unchecked":
        # Detection couldn't look at the base commit; the run is pinned to no setup.
        console.print("setup: none")
    if test_cmd:
        label = SOURCE_LABELS.get(test_cmd_source or "", "")
        if test_cmd_source == "config" and test_cmd_origin:
            label = f" (from {escape(test_cmd_origin)})"
        console.print(f"Tests: {escape(test_cmd)}{label}")  # shown in full: it's what the user approves
    elif plan.tasks and all(task.verify == "check" for task in plan.tasks):
        console.print("Tests: none (check tasks only)")
    else:
        console.print("[phil.warn]Tests: none — set test_cmd in the plan or your config[/]")
    if test_cmd_note:
        console.print(f"[phil.warn]⚠ {escape(_clip(test_cmd_note))}[/]")
    if git_note:
        console.print(f"[phil.muted]{escape(git_note)}[/]")
