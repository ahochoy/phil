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
    rendered = [line for entry in lines for line in str(entry).splitlines()]
    if len(rendered) <= BODY_LINES:
        return tuple(rendered)
    return (*rendered[: BODY_LINES - 1], "… see /more 1")


def settled_line(decision: Decision, option: Option) -> str:
    if decision.kind == "question":
        return f"Answer: {option.label}"
    if option.answer == "abort":
        prefix = "✗"
    elif option.answer == "n":
        prefix = "·"
    else:
        prefix = decision.settled_prefix
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
        # the engine always pairs abort with another option, so this only matters when abort is listed first
        default = 0 if options[0].answer != "abort" else default
    problems = [str(p) for p in escalation.get("problems") or []]
    summary = str(escalation.get("summary", ""))
    note = ""
    if reason == "attempts":
        task_id = escalation.get("task_id", "")
        title_end = summary or f"{task_id} needs another attempt"
        body = [*problems[:3], "Details: /more"]
    elif reason == "approval":
        commands = [str(c) for c in escalation.get("commands") or []]
        title_end = _TITLES[reason]
        body = [f"{escalation.get('task_id', 'The task')} wants to run:", *(f"  {c}" for c in commands),
                "Approving allows these exact commands for the rest of this run only."]
        note = ", ".join(commands)
    elif reason == "no_test_cmd":
        title_end = _TITLES[reason]
        body = [*problems, "Set [project] test_cmd in phil.toml, then retry."]
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
