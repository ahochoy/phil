"""Permission rules (spec 2026-10-13 §3.1, §3.3): a rule is a command as words, compared by program
name, with an optional trailing `*` meaning "any further arguments, or none"."""

import re
import shlex
from dataclasses import dataclass

from phil import platform

INTERPRETERS = frozenset({"python", "python3", "node", "bash", "sh", "zsh", "ruby", "perl"})
DESTRUCTIVE = frozenset({"rm", "mv", "chmod", "chown", "dd", "kill"})
# How many leading subcommand words a suggestion keeps, per program; a (program, first word) entry
# overrides the program's depth for that subcommand family.
SUBCOMMAND_DEPTH: dict[str | tuple[str, str], int] = {
    "gh": 2, "git": 1, "npm": 1, "pnpm": 1, "yarn": 1, "uv": 1, "cargo": 1, "go": 1,
    "docker": 1, "kubectl": 1,
    ("uv", "pip"): 2, ("uv", "python"): 2, ("go", "mod"): 2, ("docker", "compose"): 2,
}


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    text: str
    words: tuple[str, ...]
    open_end: bool


def program_name(arg0: str) -> str:
    """The program `arg0` names: its basename, so `/usr/bin/gh` is `gh`. On Windows either
    separator splits, and the name is lowercased without a .exe/.cmd/.bat suffix."""
    if platform.IS_WINDOWS:
        name = re.split(r"[\\/]", arg0)[-1].lower()
        for suffix in (".exe", ".cmd", ".bat"):
            if name.endswith(suffix):
                return name.removesuffix(suffix)
        return name
    return arg0.rsplit("/", 1)[-1]


def _slashed(text: str) -> str:
    """On Windows every backslash becomes `/` (as the shell policy's allowlist does), so a path
    rule survives shlex and either spelling of a path compares equal. Elsewhere `text` itself."""
    return text.replace("\\", "/") if platform.IS_WINDOWS else text


def _path_key(path: str) -> str:
    """A path as compared for equality: on Windows case-folded without a .exe/.cmd/.bat suffix."""
    path = _slashed(path)
    if platform.IS_WINDOWS:
        path = path.lower()
        for suffix in (".exe", ".cmd", ".bat"):
            path = path.removesuffix(suffix)
    return path


def parse_rule(text: str) -> Rule:
    try:
        words = shlex.split(_slashed(text))
    except ValueError as exc:
        raise RuleError(f"can't read rule {text!r}: {exc}") from exc
    open_end = bool(words) and words[-1] == "*"
    fixed = words[:-1] if open_end else words
    if not fixed:
        raise RuleError(f"rule {text!r} names no program")
    if any("*" in word for word in fixed):
        raise RuleError(f"rule {text!r}: `*` is only allowed as the last word")
    first = fixed[0] if has_separator(fixed[0]) else program_name(fixed[0])
    return Rule(text=text.strip(), words=(first, *fixed[1:]), open_end=open_end)


def has_separator(arg0: str) -> bool:
    """Whether `arg0` is a path (`./gh`, `/usr/bin/gh`) rather than a bare program name."""
    return "/" in _slashed(arg0)


def rule_matches(rule: Rule, argv: list[str]) -> bool:
    """A bare program name matches by name; a path in `argv[0]` matches only a rule that names
    that exact path (R3), so a `./gh` an agent wrote into the worktree isn't taken for `gh`."""
    if not argv:
        return False
    if has_separator(argv[0]):
        if not has_separator(rule.words[0]) or _path_key(rule.words[0]) != _path_key(argv[0]):
            return False
    elif program_name(argv[0]) != rule.words[0]:
        return False
    fixed, rest = rule.words[1:], [_slashed(arg) for arg in argv[1:]]
    if rule.open_end:
        return len(rest) >= len(fixed) and tuple(rest[: len(fixed)]) == fixed
    return tuple(rest) == fixed


def is_bare_program(rule: Rule) -> bool:
    return rule.open_end and len(rule.words) == 1


def exact_only(argv: list[str]) -> bool:
    """Commands only ever saved exactly: interpreters (their arguments are code), destructive
    commands, and a forced or destructive `git push`."""
    if not argv:
        return False
    name = program_name(argv[0])
    if name in INTERPRETERS or name in DESTRUCTIVE:
        return True
    if name != "git" or "push" not in argv[1:]:
        return False
    after_push = argv[argv.index("push", 1) + 1 :]
    return any(_forces_push(word) for word in after_push)


def _forces_push(word: str) -> bool:
    """Whether a word after `git push` forces or deletes: --force*, --mirror, --delete, --prune,
    a short-flag cluster holding f or d (`-fu`), or a `+`/`:` refspec."""
    if word.startswith(("--force", "+", ":")) or word in ("--mirror", "--delete", "--prune"):
        return True
    return re.fullmatch(r"-[A-Za-z]+", word) is not None and ("f" in word or "d" in word)


def _join(words: list[str]) -> str:
    return shlex.join(words)


def suggest_rule(argv: list[str]) -> str | None:
    """The rule "Always allow" offers for `argv` (spec §3.3). Never a bare `program *`. None when
    it can't be saved (R4): argv is empty, or a word holds `*`, which a rule can't express."""
    if not argv or any("*" in word for word in argv):
        return None
    if exact_only(argv):
        return _join(argv)
    name = program_name(argv[0])
    rest = argv[1:]
    if name in SUBCOMMAND_DEPTH:
        depth = SUBCOMMAND_DEPTH.get((name, rest[0]), SUBCOMMAND_DEPTH[name]) if rest else 0
        words: list[str] = []
        for word in rest[:depth]:
            if word.startswith("-"):
                break
            words.append(word)
        return _join([argv[0], *words]) + " *" if words else _join(argv)
    for i, word in enumerate(rest):
        if not word.startswith("-"):
            return _join([argv[0], *rest[: i + 1]]) + " *"
    return _join(argv)
