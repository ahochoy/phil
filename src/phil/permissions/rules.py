"""Permission rules (spec 2026-10-13 §3.1, §3.3): a rule is a command as words, compared by program
name, with an optional trailing `*` meaning "any further arguments, or none"."""

import re
import shlex
import unicodedata
from dataclasses import dataclass

from phil import platform

# Programs whose arguments are code, a command, or a script: only ever saved exactly (R5). Names
# are compared lowercased; versioned pythons (`python3.12`) are caught by _VERSIONED_PYTHON.
INTERPRETERS = frozenset({
    "python", "python3", "node", "deno", "bun", "php", "ruby", "perl", "lua", "rscript", "osascript",
    "pwsh", "powershell", "bash", "sh", "zsh", "dash", "ksh", "fish", "tclsh",
})
_VERSIONED_INTERPRETER = re.compile(r"(python|perl|ruby|node|php|lua|bash|tclsh)[\d.]*")
# Launchers: their arguments are another command, which the rule would not name.
LAUNCHERS = frozenset({
    "env", "sudo", "doas", "xargs", "nice", "nohup", "timeout", "time", "command", "exec", "watch",
    "npx", "bunx", "uvx", "stdbuf", "ionice", "chroot", "setsid", "script",
    "cmd", "wsl", "cscript", "wscript", "mshta",
})
# Subcommands that run something else (or, for `git config`, can install a shell alias). Found at
# any position among a command's non-flag words, since global options come first (`docker -H h
# run`, `cargo +nightly run`); over-matching only makes a command exact-only.
LAUNCHER_SUBCOMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "uv": (("run",), ("tool", "run")),
    "npm": (("exec",), ("x",)),
    "pnpm": (("exec",), ("dlx",)),
    "yarn": (("dlx",), ("exec",)),
    "cargo": (("run",),),
    "go": (("run",),),
    "docker": (
        ("run",), ("exec",), ("create",), ("container", "run"), ("container", "exec"),
        ("container", "create"), ("compose", "run"), ("compose", "exec"),
    ),
    "kubectl": (("exec",), ("run",), ("debug",), ("attach",)),
    "gh": (("api",),),
    "git": (
        ("config",), ("submodule", "foreach"), ("bisect", "run"), ("filter-branch",), ("difftool",),
        ("mergetool",),
    ),
}
# `npm x` is only the subcommand when it comes first: elsewhere `x` is just an argument.
_FIRST_WORD_ONLY = frozenset({("npm", ("x",))})
# Long options that name a command, or a place to find one, for git (matched by unique prefix).
_GIT_COMMAND_OPTIONS = (
    "--upload-pack", "--receive-pack", "--exec", "--template", "--config", "--open-files-in-pager", "--extcmd",
)
_DANGEROUS_PUSH_OPTIONS = (
    "--delete", "--mirror", "--prune", "--force", "--force-with-lease", "--force-if-includes",
    "--receive-pack", "--exec",
)
# The only programs "Always allow" offers an open-ended rule for (R7).
OPEN_FAMILIES = frozenset({
    "gh", "git", "npm", "pnpm", "yarn", "uv", "cargo", "go", "docker", "kubectl",
    "make", "just", "pytest", "tox", "nox", "ruff", "mypy", "eslint", "prettier", "tsc", "jest", "vitest",
})
DESTRUCTIVE = frozenset({
    "rm", "mv", "chmod", "chown", "dd", "kill", "rmdir", "shred", "truncate", "killall", "pkill",
})
# How many leading subcommand words a suggestion keeps, per program; a (program, first word) entry
# overrides the program's depth for that subcommand family.
SUBCOMMAND_DEPTH: dict[str | tuple[str, str], int] = {
    "gh": 2, "git": 1, "npm": 1, "pnpm": 1, "yarn": 1, "uv": 1, "cargo": 1, "go": 1,
    "docker": 1, "kubectl": 1,
    ("uv", "pip"): 2, ("uv", "python"): 2, ("go", "mod"): 2, ("docker", "compose"): 2,
    ("docker", "container"): 2,
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
    """A path as compared for equality: slashes normalised, and on Windows case-folded. The suffix
    stays, because `./gh.bat` is not `./gh.exe`."""
    path = _slashed(path)
    return path.lower() if platform.IS_WINDOWS else path


def parse_rule(text: str) -> Rule:
    """Tokenised exactly as ShellPolicy tokenises a command (R6): shlex, then per-word slashes on
    Windows. The rule is open-ended only if its raw text ends in whitespace and a bare `*`; a
    quoted or escaped `*` is an error (R4), never a wildcard."""
    try:
        words = [_slashed(word) for word in shlex.split(text)]
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
    return "/" in _slashed(arg0)


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
    fixed, rest = rule.words[1:], [_slashed(arg) for arg in argv[1:]]
    if rule.open_end:
        return len(rest) >= len(fixed) and tuple(rest[: len(fixed)]) == fixed
    return tuple(rest) == fixed


def is_bare_program(rule: Rule) -> bool:
    return rule.open_end and len(rule.words) == 1


def exact_only(argv: list[str]) -> bool:
    """Commands only ever saved exactly, and never covered by an open-ended rule (R5):
    interpreters (their arguments are code), launchers (their arguments are another command),
    destructive commands, launcher subcommands (`docker run`, `gh api`...), git forms that run a
    command, and a forced or destructive `git push`. Names are NFKC-normalised and casefolded,
    which is safe in this blocking direction."""
    if not argv:
        return False
    name = folded_name(argv[0])
    if name in INTERPRETERS or name in LAUNCHERS or name in DESTRUCTIVE or _VERSIONED_INTERPRETER.fullmatch(name):
        return True
    rest = argv[1:]
    words = [word for word in rest if not word.startswith("-")]
    for sub in LAUNCHER_SUBCOMMANDS.get(name, ()):
        starts = range(1) if (name, sub) in _FIRST_WORD_ONLY else range(len(words))
        if any(tuple(words[i : i + len(sub)]) == sub for i in starts):
            return True
    if name != "git":
        return False
    if any(_git_option_runs_command(word, words) for word in rest):
        return True
    if "push" not in rest:
        return False
    after_push = argv[argv.index("push", 1) + 1 :]
    return any(_forces_push(word) for word in after_push)


def _is_long_option_prefix(word: str, options: tuple[str, ...]) -> bool:
    """Whether `word` (before any `=value`) is a long option of `options` or an abbreviation of
    one, at least `--x` long: git accepts any unique prefix, so `--del` is `--delete`."""
    if not word.startswith("--"):
        return False
    name = word.split("=", 1)[0]
    return len(name) >= 3 and any(option.startswith(name) for option in options)


def _git_option_runs_command(word: str, words: list[str]) -> bool:
    """Whether a git option names a command to run: --upload-pack and friends, `-x`, `-O...`,
    `-c`, or `-u` on clone/ls-remote (where it is --upload-pack)."""
    if _is_long_option_prefix(word, _GIT_COMMAND_OPTIONS):
        return True
    if word == "-x" or word.startswith("-O") or (word.startswith("-c") and not word.startswith("--")):
        return True
    return word == "-u" and ("clone" in words or "ls-remote" in words)


def _forces_push(word: str) -> bool:
    """Whether a word after `git push` forces, deletes or names a command: --force*, --mirror,
    --delete, --prune, --receive-pack, --exec (or any abbreviation of them), a short-flag cluster
    holding f or d (`-fu`), or a `+`/`:` refspec."""
    if word.startswith(("--force", "+", ":")) or _is_long_option_prefix(word, _DANGEROUS_PUSH_OPTIONS):
        return True
    return re.fullmatch(r"-[A-Za-z]+", word) is not None and ("f" in word or "d" in word)


def _join(words: list[str]) -> str:
    return shlex.join(words)


def suggest_rule(argv: list[str]) -> str | None:
    """The rule "Always allow" offers for `argv` (spec §3.3, R7). Never a bare `program *`. None
    when it can't be saved (R4): argv is empty, or a word is empty or holds `*`, which a rule
    can't express. Open-ended only for a vetted program in OPEN_FAMILIES, when `exact_only` is
    False and no flag cut the subcommand short; otherwise the exact command."""
    if not argv or any(not word or "*" in word for word in argv):
        return None
    exact = _join(argv)
    name = folded_name(argv[0])
    if not argv[0].isascii() or name not in OPEN_FAMILIES or exact_only(argv):
        return exact
    rest = argv[1:]
    if name in SUBCOMMAND_DEPTH:
        depth = SUBCOMMAND_DEPTH.get((name, rest[0]), SUBCOMMAND_DEPTH[name]) if rest else 0
        words: list[str] = []
        for word in rest[:depth]:
            if word.startswith("-"):
                return exact
            words.append(word)
        return _join([argv[0], *words]) + " *" if words else exact
    for i, word in enumerate(rest):
        if not word.startswith("-"):
            return _join([argv[0], *rest[: i + 1]]) + " *"
    return exact
