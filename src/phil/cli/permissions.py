"""`phil permissions` (spec 2026-10-13 §3.7): list the rules in effect, check a command, add or
remove a rule, and trust or ignore the shell settings a repo's phil.toml ships."""

import shlex
import sys
from datetime import date
from pathlib import Path

import typer
from rich.text import Text

from phil.chat.decision import repo_shell_lines, repo_shell_title
from phil.config import PhilConfig, global_config_path
from phil.permissions.listing import listing_lines, policy_entries, problem_lines, shown_path
from phil.permissions.load import policy_or_fallback, repo_shell_settings, rule_label
from phil.permissions.rules import OPEN_FAMILIES, RuleError, exact_only, folded_name, is_bare_program, parse_rule
from phil.permissions.store import (
    POLICY_HEADER,
    PolicyWriteError,
    RepoShellSettings,
    add_rule,
    file_problems,
    normalised_rule,
    record_repo_decision,
    remove_rule,
    repo_shell_hash,
)
from phil.repo import RepoInfo
from phil.store.paths import ProjectPaths, global_policy_path
from phil.text import printable
from phil.ui.theme import make_console
from phil.workspace.shell import ShellPolicy

permissions_app = typer.Typer(help="List, check and change the rules for agents' shell commands.")
console = make_console()

OUTCOME_WORDS = {"allow": "runs", "ask": "asks", "deny": "blocked", "forbidden": "refused"}
PLAIN_ARGUMENTS_NOTE = 'Note: "*" covers plain arguments only (no options).'
NOTHING_TO_TRUST = "phil.toml has no [shell] allow, pass_env or bash settings."
NEEDS_YES = "Run with --yes to trust these without a prompt."
EVERYWHERE_HELP = "Use the file for every repo (~/.phil/policy.toml) instead of this repo's."


def _say(line: str) -> None:
    console.print(Text(line), soft_wrap=True)


def _fail(line: str) -> None:
    _say(line)
    raise typer.Exit(1)


def _project(ctx: typer.Context) -> tuple[RepoInfo, PhilConfig]:
    from phil.cli import main  # main registers this app, so it's imported here, not at the top

    info = main._resolve(ctx)
    return info, main._load_config(info.root, ctx.obj.get("overrides", []))


def _target(info: RepoInfo, everywhere: bool) -> tuple[Path, str]:
    """The policy file a change goes to, and its scope as the output names it."""
    if everywhere:
        return global_policy_path(), "everywhere"
    return ProjectPaths(info.slug).policy_path, info.root.name


def _refuse_broken(path: Path) -> None:
    problems = file_problems(path)
    if problems:
        _fail(f"{shown_path(path)} can't be read ({printable(problems[0].message)}). Fix it by hand, then try again.")


@permissions_app.callback(invoke_without_command=True)
def listing(ctx: typer.Context) -> None:
    """List every rule in effect, by where it comes from."""
    if ctx.invoked_subcommand is not None:
        return
    info, config = _project(ctx)
    for line in listing_lines(info.root, ProjectPaths(info.slug), config):
        _say(line)


@permissions_app.command()
def check(ctx: typer.Context, command: str = typer.Argument(..., help="The command, quoted.")) -> None:
    """Show what Phil would do with a command, and why."""
    info, config = _project(ctx)
    paths = ProjectPaths(info.slug)
    policy = policy_or_fallback(info.root, paths, config)
    for line in problem_lines(policy, (global_policy_path(), paths.policy_path)):
        _say(line)
    decision = ShellPolicy(policy, root=info.root).decide(command)
    _say(f"{OUTCOME_WORDS[decision.outcome]} · {decision.label} · {printable(decision.reason)}")


def _needs_yes(rule, kind: str) -> str | None:
    """Why saving this open rule needs --yes, or None: a bare `program *`, or (allowing) an open rule
    for a program outside the vetted families (R7)."""
    if not rule.open_end:
        return None
    program = printable(rule.words[0])
    if is_bare_program(rule):
        return f'"{program} *" allows {program} with any plain arguments.'
    if kind == "allow" and folded_name(rule.words[0]) not in OPEN_FAMILIES:
        fixed = printable(shlex.join(rule.words))
        return f'"{fixed} *" allows {fixed} with any plain arguments, and Phil hasn\'t vetted {program}.'
    return None


def _never_matches(rule, kind: str) -> bool:
    """An open allow rule whose fixed words are exact-only (`sudo *`, `python *`, `git push -f *`):
    Phil saves such commands exactly, so it never matches anything (R5)."""
    return kind == "allow" and rule.open_end and exact_only(list(rule.words))


DEAD_RULE = '"{rule}" can never match — Phil saves commands like this exactly.'


def _add(ctx: typer.Context, kind: str, rule_text: str, everywhere: bool, yes: bool) -> None:
    info, _ = _project(ctx)
    try:
        rule = parse_rule(rule_text)
    except RuleError as exc:
        _fail(f"Not a valid rule: {printable(exc)}")
    normal = printable(normalised_rule(rule_text))
    decision = ShellPolicy(None).decide(shlex.join(rule.words))
    if decision.outcome == "forbidden":
        _fail(f'"{normal}" is forbidden, so it can\'t be saved ({printable(decision.reason)}).')
    dead = _never_matches(rule, kind)
    reason = DEAD_RULE.format(rule=normal) if dead else _needs_yes(rule, kind)
    if reason is not None and not yes:
        _fail(f"{reason} Add --yes to save it anyway.")
    path, scope = _target(info, everywhere)
    _refuse_broken(path)
    try:
        add_rule(path, kind, rule_text, "manual", today=date.today(), header=POLICY_HEADER.format(scope=scope))
    except PolicyWriteError as exc:
        _fail(printable(exc))
    _say(f'Added {kind} rule "{normal}" for {printable(scope)}.')
    if dead:
        _say(f"Note: {DEAD_RULE.format(rule=normal)} Saved anyway.")
    elif kind == "allow" and rule.open_end:
        _say(PLAIN_ARGUMENTS_NOTE)


@permissions_app.command()
def allow(
    ctx: typer.Context,
    rule: str = typer.Argument(..., help='The rule, quoted, e.g. "gh pr checks *".'),
    everywhere: bool = typer.Option(False, "--everywhere", help=EVERYWHERE_HELP),
    yes: bool = typer.Option(False, "--yes", help="Save a broad rule such as `program *` without asking."),
) -> None:
    """Allow commands matching a rule."""
    _add(ctx, "allow", rule, everywhere, yes)


@permissions_app.command()
def deny(
    ctx: typer.Context,
    rule: str = typer.Argument(..., help='The rule, quoted, e.g. "git push *".'),
    everywhere: bool = typer.Option(False, "--everywhere", help=EVERYWHERE_HELP),
    yes: bool = typer.Option(False, "--yes", help="Save a broad rule such as `program *` without asking."),
) -> None:
    """Block commands matching a rule, whatever else allows them."""
    _add(ctx, "deny", rule, everywhere, yes)


def _program(text: str) -> str | None:
    try:
        words = shlex.split(text)
    except ValueError:
        return None
    return folded_name(words[0]) if words else None


@permissions_app.command()
def remove(
    ctx: typer.Context,
    rule: str = typer.Argument(..., help="The rule, quoted, as `phil permissions` lists it."),
    everywhere: bool = typer.Option(False, "--everywhere", help=EVERYWHERE_HELP),
) -> None:
    """Remove an allow or deny rule."""
    info, config = _project(ctx)
    path, _scope = _target(info, everywhere)
    _refuse_broken(path)
    try:
        shown = printable(normalised_rule(rule))
    except RuleError:
        shown = printable(rule.strip())
    try:
        removed = remove_rule(path, rule)
    except PolicyWriteError as exc:
        _fail(printable(exc))
    if removed:
        _say(f'Removed {" and ".join(removed) + " " if len(removed) > 1 else ""}"{shown}".')
        return
    policy = policy_or_fallback(info.root, ProjectPaths(info.slug), config)
    if rule.strip() in [entry.rule.text for entry in policy.allow if entry.scope == "config"]:
        _fail(f'"{printable(rule.strip())}" comes from {shown_path(global_config_path())} [shell] allow — edit it there.')
    program = _program(rule)
    similar = [text for _, text, _ in policy_entries(path) if program is not None and _program(text) == program]
    hint = " Similar: " + ", ".join(f'"{printable(text)}"' for text in similar) if similar else ""
    _fail(f'No rule "{shown}".{hint}')


def _settings_named(settings: RepoShellSettings) -> str:
    """`2 rules`, `1 rule and pass_env`, `2 rules, pass_env and bash`..."""
    parts = []
    if settings.allow:
        parts.append(f"{len(settings.allow)} rule{'s' if len(settings.allow) != 1 else ''}")
    parts += [name for name, set_ in (("pass_env", bool(settings.pass_env)), ("bash", settings.bash is not None)) if set_]
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _on_a_terminal() -> bool:
    """Whether someone can answer a prompt here (patched in tests)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _show(repo: str, settings: RepoShellSettings) -> None:
    """What's being trusted, as the chat's trust prompt shows it (spec §3.5, R14): each allow
    pattern verbatim with its label, then pass_env and bash. Display text only."""
    rules = list(settings.allow)
    _say(repo_shell_title(repo, rules))
    for line in repo_shell_lines(rules, [rule_label(rule) for rule in rules], settings.pass_env, settings.bash):
        _say(f"  {line}")


def _decide(ctx: typer.Context, decision: str, yes: bool = False) -> None:
    info, _ = _project(ctx)
    try:
        settings = repo_shell_settings(info.root)
    except Exception as exc:
        _fail(f"phil.toml can't be read ({printable(exc)}). Fix it, then try again.")
    if settings.empty:
        _say(NOTHING_TO_TRUST)
        return
    path = ProjectPaths(info.slug).policy_path
    _refuse_broken(path)
    _show(info.root.name, settings)
    if decision == "trust" and not yes:
        if not _on_a_terminal():
            _fail(NEEDS_YES)
        answer = typer.prompt(f"Trust these for {printable(info.root.name)}? [y/N]", default="", show_default=False)
        if answer.strip().lower() not in ("y", "yes"):
            _say("Nothing recorded.")
            return
    try:
        # The hash of exactly what was shown above, even if phil.toml changed since.
        record_repo_decision(path, decision, repo_shell_hash(settings))
    except PolicyWriteError as exc:
        _fail(printable(exc))
    verb = "Trusted" if decision == "trust" else "Ignoring"
    _say(f"{verb} {_settings_named(settings)} from phil.toml.")


@permissions_app.command()
def trust(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", help="Trust them without asking (for scripts and no terminal)."),
) -> None:
    """Show the shell settings in this repo's phil.toml (allow, pass_env and bash), then use them."""
    _decide(ctx, "trust", yes)


@permissions_app.command()
def ignore(ctx: typer.Context) -> None:
    """Show the shell settings in this repo's phil.toml, keep them off, and stop asking about them."""
    _decide(ctx, "ignore")
