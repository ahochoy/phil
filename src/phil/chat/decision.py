"""What the chat asks, as data (spec 2026-10-07 callouts §3.1): a Decision is rendered as a
callout and answered from a menu. Each option's `answer` is the text the chat's existing input
handlers already accept, so picking from the menu and typing are the same thing to the chat."""

from collections.abc import Sequence
from dataclasses import dataclass

from phil.text import printable

BODY_LINES = 8
SEE_MORE = "… see /more 1"  # a capped body's last line: the full body is detail 1
DETAILS = "Details: /more 1"  # a body's pointer at detail 1 when nothing was cut (an attempts pause)

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
    return (*rendered[: BODY_LINES - 1], SEE_MORE)


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


# An approval pause's own labels (spec 2026-10-13 §3.4, R15); other pauses keep LABELS.
_APPROVAL_LABELS = {"approve": "Allow for this run", "deny": "Deny", "abort": "Stop the run"}


def approval_lines(escalation: dict) -> list[str]:
    """Each denied command in full, with its label and the rule it would save (R15):
    `  <command>  (<label>)  → saves <rule>`, or `→ can't be saved`. Display text only: control
    characters show as `?`."""
    commands = [str(c) for c in escalation.get("commands") or []]
    labels = [str(label) for label in escalation.get("labels") or []]
    saves = list(escalation.get("saves") or [])
    lines = []
    for index, command in enumerate(commands):
        line = f"  {printable(command)}"
        if index < len(labels):
            line += f"  ({labels[index]})"
        if index < len(saves):
            rule = saves[index]
            line += f"  → saves {printable(rule)}" if rule is not None else "  → can't be saved"
        lines.append(line)
    return lines


def _approval_options(escalation: dict, words: list[str]) -> tuple[Option, ...]:
    """The approval's answers. With more than one rule, the "always" answers say how many they save
    (N2), so a box that can't show every rule still says what choosing one does."""
    count = len(escalation.get("rules") or [])
    rules = f" ({count} rules)" if count > 1 else ""
    labels = {
        **_APPROVAL_LABELS,
        "always_repo": f"Always allow in {printable(escalation.get('repo', 'this repo'))}{rules}",
        "always_everywhere": f"Always allow everywhere{rules}",
    }
    return tuple(Option(labels.get(w) or LABELS.get(w, w.capitalize()), w) for w in words)


def pause_decision(run_id: str, escalation: dict) -> Decision:
    reason = str(escalation.get("reason", ""))
    words = [str(w) for w in escalation.get("options") or ["abort"]]
    words = [w for w in words if w != "abort"] + ["abort"]  # abort always last
    if reason == "approval":
        options = _approval_options(escalation, words)
    else:
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
        body = [*problems[:3], DETAILS]
    elif reason == "approval":
        commands = [str(c) for c in escalation.get("commands") or []]
        title_end = _TITLES[reason]
        body = [f"{escalation.get('task_id', 'The task')} wants to run:", *approval_lines(escalation)]
        note = ", ".join(printable(command) for command in commands)
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
    # An approval's body is never capped here: the callout renderer counts the entries it leaves out (N2).
    shown = tuple(body) if kind == "approval" else _capped(body)
    return Decision(kind, f"⏸ {run_id} needs you · {title_end}", shown, options, default, note=note)


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


def repo_shell_title(repo: str, rules: Sequence[str]) -> str:
    """The trust prompt's title: what the repo's phil.toml settings do (F9). Display text only."""
    name = printable(repo)
    return f"{name}'s phil.toml allows these commands" if rules else f"{name}'s phil.toml changes how commands run"


def repo_shell_lines(rules: Sequence[str], labels: Sequence[str], pass_env: Sequence[str] = (),
                     bash: str | None = None) -> list[str]:
    """Everything a repo's phil.toml asks to be trusted (spec 2026-10-13 §3.5, R14): each allow
    pattern, verbatim, with its label, then pass_env and bash when set. Display text only: hidden
    characters show as `?`."""
    shown = [printable(rule) for rule in rules]
    width = max((len(rule) for rule in shown), default=0)
    lines = [f"{rule.ljust(width)}  {label}" if label else rule for rule, label in zip(shown, labels, strict=True)]
    if pass_env:
        lines.append(f"passes these environment variables to agent commands: {', '.join(printable(n) for n in pass_env)}")
    if bash is not None:
        lines.append(f"runs every command through: {printable(bash)}")
    return lines


def repo_shell_decision(repo: str, rules: Sequence[str], labels: Sequence[str], pass_env: Sequence[str] = (),
                        bash: str | None = None) -> Decision:
    """Whether to trust the shell settings a repo's phil.toml ships, asked before plan approval
    and before a quick run starts. A long body is capped; the full list is detail 1."""
    name = printable(repo)
    return Decision(
        "confirm", repo_shell_title(repo, rules), _capped(repo_shell_lines(rules, labels, pass_env, bash)),
        (Option(f"Trust these for {name}", "trust"), Option("Ignore them", "ignore")),
    )


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
