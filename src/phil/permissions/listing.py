"""What `phil permissions` and the chat's `/permissions` print (spec 2026-10-13 §3.7): any problem
first, then the rules by where they come from. Display text only: every rule and value is shown
with hidden characters as `?`."""

import os
import tomllib
from pathlib import Path

from phil.config import DEFAULT_SOURCE, REPO_SOURCE, SET_SOURCE, PhilConfig, global_config_path, load_config
from phil.permissions.load import policy_or_fallback, rule_label
from phil.permissions.store import KINDS, PolicyProblem, PolicySet, file_problems
from phil.store.paths import ProjectPaths, global_policy_path
from phil.text import printable

BUILT_IN_LINE = "Built-in: read-only commands (ls, grep, git log, gh pr view, npm ls, …) run without asking."
BUILT_IN_DEFAULTS = "built-in defaults (no config entry)"
CHANGE_HINT = "Change rules with `phil permissions allow|deny|remove`."
_SAVED_OFF = " — saved allow rules are off until it's fixed"


def shown_path(path: Path) -> str:
    """`path` as the listing shows it: under the home folder, written from `~`."""
    try:
        return os.path.join("~", path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def policy_entries(path: Path) -> list[tuple[str, str, str]]:
    """Each `(kind, rule, note)` written in a policy file, allow entries first; `note` is where the
    rule came from and when it was added. Empty when the file is missing or can't be parsed (the
    problem is reported separately)."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = []
    for kind in KINDS:
        items = data.get(kind)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("rule"), str):
                note = " · ".join(str(item[key]) for key in ("from", "added") if item.get(key) not in (None, ""))
                entries.append((kind, item["rule"], note))
    return entries


def problem_lines(policy: PolicySet, policy_files: tuple[Path, ...]) -> list[str]:
    """One `⚠` line per problem; a problem in a policy file says that saved allow rules are off."""
    broken: set[PolicyProblem] = {problem for path in policy_files for problem in file_problems(path)}
    lines = []
    for problem in policy.problems:
        where = shown_path(problem.path) + (f":{problem.line}" if problem.line else "")
        suffix = _SAVED_OFF if problem in broken else ""
        lines.append(f"⚠ {printable(where)}: {printable(problem.message)}{suffix}")
    return lines


def _rows(entries: list[tuple[str, str, str]]) -> list[str]:
    """Aligned rows: the kind column as wide as its widest (`allow`, `deny`, `pass_env`, `bash`)."""
    shown = [(kind, printable(rule), printable(note)) for kind, rule, note in entries]
    kinds = max((len(kind) for kind, _, _ in shown), default=0)
    width = max((len(rule) for _, rule, _ in shown), default=0)
    return [f"  {kind.ljust(max(kinds, 5))}  {rule.ljust(width)}   {note}".rstrip() for kind, rule, note in shown]


def _repo_section(repo: str, policy: PolicySet, policy_files: tuple[Path, ...]) -> list[str]:
    settings = policy.repo_shell
    if settings.empty:
        return []
    status = {"trust": "trusted", "ignore": "ignored"}.get(policy.repo_decision or "", "not trusted — phil permissions trust")
    if policy.repo_decision == "trust" and not policy.repo_trusted:
        broken = next((path for path in policy_files if file_problems(path)), None)
        if broken is not None:
            status = f"trusted, but off until {printable(shown_path(broken))} is fixed"
    rows = [("allow", rule, rule_label(rule)) for rule in settings.allow]
    if settings.pass_env:
        rows.append(("pass_env", ", ".join(settings.pass_env), ""))
    if settings.bash is not None:
        rows.append(("bash", settings.bash, ""))
    return [f"{repo} phil.toml ({status}):", *_rows(rows)]


def _config_header(repo_root: Path, config: PhilConfig) -> str:
    """Where config's `[shell] allow` comes from: the defaults, `~/.phil/config.toml` or `--set`.
    The repo's phil.toml never counts (it needs trust), so its layer is left out."""
    try:
        if config.sources.get("shell.allow") == REPO_SOURCE:
            config = load_config(repo_root, include_repo=False)
        source = config.sources.get("shell.allow", DEFAULT_SOURCE)
    except Exception:
        source = DEFAULT_SOURCE  # the policy falls back to the defaults too
    if source == DEFAULT_SOURCE:
        return f"Config ({BUILT_IN_DEFAULTS}):"
    where = "--set" if source == SET_SOURCE else shown_path(global_config_path())
    return f"Config ({printable(where)} [shell] allow):"


def listing_lines(repo_root: Path, paths: ProjectPaths, config: PhilConfig) -> list[str]:
    """Problems first, then built-in, everywhere, this repo, the repo's phil.toml settings and
    config, leaving out any section with nothing in it except built-in."""
    policy = policy_or_fallback(repo_root, paths, config)
    repo = printable(repo_root.name)
    everywhere, project = global_policy_path(), paths.policy_path
    lines = [*problem_lines(policy, (everywhere, project)), BUILT_IN_LINE]
    for header, path in ((f"Everywhere ({shown_path(everywhere)}):", everywhere),
                         (f"{repo} ({shown_path(project)}):", project)):
        entries = policy_entries(path)
        if entries:
            lines += [printable(header), *_rows(entries)]
    lines += _repo_section(repo, policy, (everywhere, project))
    patterns = [entry.rule.text for entry in policy.allow if entry.scope == "config"]
    if patterns:
        lines.append(_config_header(repo_root, config))
        lines += _rows([("allow", pattern, "") for pattern in patterns])
    return lines
