"""Permission rules (spec 2026-10-13 §3.1, §3.3): a rule is a command as words, compared by program
name, with an optional trailing `*` meaning "any further plain arguments, or none".

Policy (rulings R5, R7, R8, R9 in the plan's constraints):
- The main rule (R9): a `*` covers plain arguments only, words that don't start with `-` and don't
  contain `=`. Any option or `NAME=value` after a rule's fixed words makes the command ask again,
  so no option, however dangerous, hides behind a `*`. That's why there are no option lists here.
- Open-ended suggestions come only from the vetted families in OPEN_FAMILIES, and only when every
  word after the subcommand is a plain argument. Every other command is suggested exactly.
- Dangerous programs and subcommands are exact-only (`exact_only`): interpreters, launchers,
  destructive programs, and, within a family, its FAMILY_DANGERS subcommands or a remove verb.
  Their plain arguments are dangerous too (code, a command, a target), so no rule ending in `*`
  ever matches them, whoever wrote it. `exact_only` also keeps "Always allow everywhere" out.
- A hand-written open rule (`terraform *`, `gh pr *`) trusts every plain argument the program
  accepts. Task 7 asks for `--yes` before saving one for a program outside the families.
"""

import re
import shlex
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from phil import platform
from phil.text import HIDDEN_CATEGORIES

# Programs whose arguments are code, a command, or a script: only ever saved exactly (R5). Names
# are compared folded; versioned ones (`python3.12`, `node20`) are caught by _VERSIONED_INTERPRETER.
INTERPRETERS = frozenset({
    "python", "python3", "py", "pypy", "pypy3", "ipython", "node", "deno", "bun", "ts-node", "tsx",
    "java", "php", "ruby", "perl", "lua", "rscript", "osascript",
    "pwsh", "powershell", "bash", "sh", "zsh", "dash", "ksh", "fish", "tclsh",
})
_VERSIONED_INTERPRETER = re.compile(r"(python|perl|ruby|node|php|lua|bash|tclsh)[\d.]*")
# Launchers: their arguments are another command, which the rule would not name.
LAUNCHERS = frozenset({
    "env", "sudo", "doas", "xargs", "nice", "nohup", "timeout", "time", "command", "exec", "watch",
    "npx", "bunx", "uvx", "stdbuf", "ionice", "chroot", "setsid", "script",
    "cmd", "wsl", "cscript", "wscript", "mshta",
})
DESTRUCTIVE = frozenset({
    "rm", "mv", "chmod", "chown", "dd", "kill", "rmdir", "shred", "truncate", "killall", "pkill",
})

# The only programs "Always allow" offers an open-ended rule for (R7).
OPEN_FAMILIES = frozenset({
    "gh", "git", "npm", "pnpm", "yarn", "uv", "cargo", "go", "docker", "kubectl",
    "make", "just", "pytest", "tox", "nox", "ruff", "mypy", "eslint", "prettier", "tsc", "jest", "vitest",
})
# Docker's management groups: `docker volume ls` and `docker volume rm` are different commands.
_DOCKER_GROUPS = (
    "builder", "buildx", "compose", "config", "container", "context", "image", "manifest", "network",
    "node", "plugin", "secret", "service", "stack", "swarm", "system", "trust", "volume",
)
# How many leading subcommand words a suggestion keeps, per program; a (program, first word) entry
# overrides the program's depth for that subcommand family.
SUBCOMMAND_DEPTH: dict[str | tuple[str, str], int] = {
    "gh": 2, "git": 1, "npm": 1, "pnpm": 1, "yarn": 1, "uv": 1, "cargo": 1, "go": 1,
    "docker": 1, "kubectl": 1,
    ("uv", "pip"): 2, ("uv", "python"): 2, ("uv", "tool"): 2, ("go", "mod"): 2,
    **{("docker", group): 2 for group in _DOCKER_GROUPS},
}

# R8: in a family command, any of these among its words makes it exact-only, so approving one
# deletion never approves another.
REMOVE_VERBS = frozenset({
    "delete", "del", "rm", "remove", "prune", "uninstall", "unpublish", "destroy", "purge", "drop",
    "kill", "clean", "reset",
})


def is_plain_argument(word: str) -> bool:
    """What a rule's `*` covers (R9): a word that is neither an option (`-`, `--`, `-x`, `--opt`)
    nor a `NAME=value` assignment."""
    return not word.startswith("-") and "=" not in word


def _git_push_is_destructive(rest: list[str], words: list[str]) -> bool:
    """A `+`/`:` refspec after `git push` (force or delete a branch). These are plain arguments, so
    R9 doesn't stop them; this check does. A forcing or deleting option (`-f`, `-4d`, `--force*`,
    `--mirror`, `--delete`, `--prune`) can't reach an open rule (R9), but is still exact-only, so
    that "Always allow everywhere" is never offered for a forced push."""
    if "push" not in rest:
        return False
    for word in rest[rest.index("push") + 1 :]:
        if word.startswith(("+", ":", "--force", "--mirror", "--delete", "--prune")):
            return True
        if re.fullmatch(r"-[A-Za-z0-9]+", word) and any(ch in word for ch in "fd"):
            return True
    return False


@dataclass(frozen=True)
class Dangers:
    """One family's danger list. `subcommands` are word sequences found at any position among the
    command's non-flag words, since global options come first (`docker -H h run`, `cargo +nightly
    run`); over-matching only makes a command exact-only. `first_word` sequences count only as
    the first non-flag word, where elsewhere they're ordinary arguments (`npm install x`).
    `check(rest, words)` flags any other dangerous form."""

    subcommands: tuple[tuple[str, ...], ...] = ()
    first_word: tuple[tuple[str, ...], ...] = ()
    check: Callable[[list[str], list[str]], bool] | None = None


# Each vetted family's danger list (R5, R7): subcommands that run another command, build with host
# access, copy into a workload, or set config that names a command to run later.
FAMILY_DANGERS: dict[str, Dangers] = {
    "git": Dangers(
        subcommands=(
            ("config",), ("submodule", "foreach"), ("bisect", "run"), ("filter-branch",), ("difftool",),
            ("mergetool",), ("send-email",), ("instaweb",), ("daemon",),
        ),
        check=_git_push_is_destructive,
    ),
    "gh": Dangers(subcommands=(("api",), ("config", "set"), ("codespace", "ssh"), ("cs", "ssh"))),
    "npm": Dangers(subcommands=(("exec",), ("explore",), ("config",)), first_word=(("x",), ("set",))),
    "pnpm": Dangers(subcommands=(("exec",), ("dlx",), ("config",))),
    "yarn": Dangers(subcommands=(("exec",), ("dlx",), ("config",))),
    "uv": Dangers(subcommands=(("run",),)),  # also `uv tool run`
    "cargo": Dangers(subcommands=(("run",),)),
    "go": Dangers(subcommands=(("run",), ("tool",))),
    # `run`, `exec`, `create` and `build` anywhere also cover the `container`, `compose`, `image`,
    # `buildx` and `builder` forms.
    "docker": Dangers(subcommands=(("run",), ("exec",), ("create",), ("build",), ("bake",))),
    "kubectl": Dangers(subcommands=(("exec",), ("run",), ("debug",), ("attach",), ("cp",), ("config",))),
}


def folded_name(arg0: str) -> str:
    """The program name as compared in the blocking direction: NFKC-normalised and casefolded, so
    `ſudo` and `ﬁnd` (which a case-insensitive filesystem resolves) are `sudo` and `find`."""
    return unicodedata.normalize("NFKC", program_name(arg0)).casefold()


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    text: str
    words: tuple[str, ...]
    open_end: bool


def program_name(arg0: str) -> str:
    """The program `arg0` names: its basename, so `/usr/bin/gh` is `gh`. On Windows either
    separator splits, and the name is lowercased without trailing dots (which Windows ignores) or
    a .exe/.com/.cmd/.bat suffix, so `git.exe.` and `git.com` are `git` too. Allow and deny
    matching both use it."""
    if platform.IS_WINDOWS:
        name = re.split(r"[\\/]", arg0)[-1].lower().rstrip(".")
        for suffix in (".exe", ".com", ".cmd", ".bat"):
            if name.endswith(suffix):
                return name.removesuffix(suffix).rstrip(".")
        return name
    return arg0.rsplit("/", 1)[-1]


def slashed(text: str) -> str:
    """On Windows every backslash becomes `/`, so a path rule survives shlex and either spelling of
    a path compares equal; elsewhere `text` itself. The one slash normalisation: rules, allowlist
    matching and the shell policy's containment check all use it (R6)."""
    return text.replace("\\", "/") if platform.IS_WINDOWS else text


def _path_key(path: str) -> str:
    """A path as compared for equality: slashes normalised, and on Windows case-folded. The suffix
    stays, because `./gh.bat` is not `./gh.exe`."""
    path = slashed(path)
    return path.lower() if platform.IS_WINDOWS else path


def parse_rule(text: str) -> Rule:
    """Tokenised exactly as ShellPolicy tokenises a command (R6): shlex, then per-word slashes on
    Windows. The rule is open-ended only if its raw text ends in whitespace and a bare `*`; a
    quoted or escaped `*` is an error (R4), never a wildcard. Control characters (newline, tab,
    escape...) are an error: they have no place in a rule and could forge lines in a file."""
    if any(unicodedata.category(char) in HIDDEN_CATEGORIES for char in text):
        raise RuleError(f"rule {text!r} contains a control or hidden character")
    try:
        words = [slashed(word) for word in shlex.split(text)]
    except ValueError as exc:
        raise RuleError(f"can't read rule {text!r}: {exc}") from exc
    open_end = bool(words) and words[-1] == "*" and re.search(r"\s\*$", text.strip()) is not None
    fixed = words[:-1] if open_end else words
    if not fixed or not fixed[0]:
        raise RuleError(f"rule {text!r} names no program")
    if any("*" in word for word in fixed):
        raise RuleError(f"rule {text!r}: `*` is only allowed, bare, as the last word")
    first = fixed[0] if has_separator(fixed[0]) else program_name(fixed[0])
    return Rule(text=text.strip(), words=(first, *fixed[1:]), open_end=open_end)


def has_separator(arg0: str) -> bool:
    """Whether `arg0` is a path (`./gh`, `/usr/bin/gh`) rather than a bare program name."""
    return "/" in slashed(arg0)


def rule_matches(rule: Rule, argv: list[str]) -> bool:
    """A bare program name matches by name; a path in `argv[0]` matches only a rule that names
    that exact path (R3), so a `./gh` an agent wrote into the worktree isn't taken for `gh`."""
    if not argv or (rule.open_end and exact_only(argv)):  # R5
        return False
    if has_separator(argv[0]):
        if not has_separator(rule.words[0]) or _path_key(rule.words[0]) != _path_key(argv[0]):
            return False
    elif not argv[0].isascii() or program_name(argv[0]) != rule.words[0]:
        return False
    fixed, rest = rule.words[1:], [slashed(arg) for arg in argv[1:]]
    if rule.open_end:  # R9: the `*` covers plain arguments only
        return (tuple(rest[: len(fixed)]) == fixed and len(rest) >= len(fixed)
                and all(is_plain_argument(word) for word in rest[len(fixed) :]))
    return tuple(rest) == fixed


def deny_matches(rule: Rule, argv: list[str]) -> bool:
    """A deny rule's matcher (R13, as amended), which errs toward matching because it blocks. The
    program is compared by folded name whatever its path (`/usr/bin/git`, `GIT`). For an open rule,
    its fixed words need only appear in order among the command's later words, so global options
    (`git -C x push -f`, `gh --repo o/r pr merge 1`) don't hide the subcommand, and any other words,
    options included, may come between and after. An exact rule is a whole-command match. No
    command is exempt as exact-only."""
    if not argv:
        return False
    if folded_name(argv[0]) != folded_name(rule.words[0]):
        return False
    fixed, rest = rule.words[1:], [slashed(arg) for arg in argv[1:]]
    if rule.open_end:
        remaining = iter(rest)
        return all(word in remaining for word in fixed)  # a subsequence: in order, gaps allowed
    return tuple(rest) == fixed


def is_bare_program(rule: Rule) -> bool:
    return rule.open_end and len(rule.words) == 1


def exact_only(argv: list[str]) -> bool:
    """Commands only ever saved exactly, never covered by an open-ended rule (R5, R8), and never
    offered "everywhere": interpreters (their arguments are code), launchers (their arguments are
    another command), destructive programs, and a family command on its FAMILY_DANGERS list or
    holding a remove verb. Options aren't checked here: R9 keeps every option out of a `*`. Names
    are NFKC-normalised and casefolded, which is safe in this blocking direction."""
    if not argv:
        return False
    name = folded_name(argv[0])
    if name in INTERPRETERS or name in LAUNCHERS or name in DESTRUCTIVE or _VERSIONED_INTERPRETER.fullmatch(name):
        return True
    rest = argv[1:]
    if name not in OPEN_FAMILIES:
        return False
    words = [word for word in rest if not word.startswith("-")]
    if any(word in REMOVE_VERBS for word in words):  # R8
        return True
    dangers = FAMILY_DANGERS.get(name)
    if dangers is None:
        return False
    if any(_has_sequence(words, sub) for sub in dangers.subcommands):
        return True
    if any(tuple(words[: len(sub)]) == sub for sub in dangers.first_word):
        return True
    return dangers.check is not None and dangers.check(rest, words)


def _has_sequence(words: list[str], sub: tuple[str, ...]) -> bool:
    return any(tuple(words[i : i + len(sub)]) == sub for i in range(len(words)))


def suggest_rule(argv: list[str]) -> str | None:
    """The rule "Always allow" offers for `argv` (spec §3.3, R7). Never a bare `program *`. None
    when it can't be saved: argv is empty, a word is empty or holds `*`, which a rule can't
    express (R4), or the program name isn't ASCII, which a bare-name rule never matches (N1).
    Open-ended only for a vetted program in OPEN_FAMILIES, when `exact_only` is False, the command
    has all its subcommand words with no flag among them, and every word after them is a plain
    argument (R9); otherwise the exact command."""
    if not argv or any(not word or "*" in word for word in argv) or not program_name(argv[0]).isascii():
        return None
    exact = _join(argv)
    name = folded_name(argv[0])
    # The program name is ASCII, but a path to it may not be (`./ſ/gh`): such a path is saved exactly.
    if not argv[0].isascii() or name not in OPEN_FAMILIES or exact_only(argv):
        return exact
    rest = argv[1:]
    if name in SUBCOMMAND_DEPTH:
        depth = SUBCOMMAND_DEPTH.get((name, rest[0]), SUBCOMMAND_DEPTH[name]) if rest else 0
        fixed = rest[:depth]
        # Stopping at a group word (`docker plugin`, `gh repo`) would give a group-wide rule (Q2).
        if not fixed or len(fixed) < depth or any(word.startswith("-") for word in fixed):
            return exact
    else:
        first = next((i for i, word in enumerate(rest) if not word.startswith("-")), None)
        if first is None:
            return exact
        fixed = rest[: first + 1]
    if not all(is_plain_argument(word) for word in rest[len(fixed) :]):  # R9
        return exact
    return _join([argv[0], *fixed]) + " *"


def _join(words: list[str]) -> str:
    return shlex.join(words)
