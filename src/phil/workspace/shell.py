import fnmatch
import os
import re
import shlex
import signal
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

_ACTIVE_GROUPS: set[int] = set()

_FORBIDDEN = set(";&|$`<>\n")

_GLOB_CHARS = set("*?[")


def literal_pattern(command: str) -> str:
    """Allowlist pattern that matches exactly this command (glob characters escaped)."""
    return "".join(f"[{char}]" if char in _GLOB_CHARS else char for char in command)


_RISKY_PROGRAMS = {"git", "npm", "uv", "python", "python3"}
_RISKY_PREFIXES = ("--output", "--no-index", "--ext-diff", "--prefix")
_RISKY_EXACT = {"-c", "-e"}

# Commands that only read state and are allowed by default, with no [shell] allow entry needed.
# A `git ...` entry matches on argv[1] (the subcommand); every other entry matches on argv[0].
READ_ONLY: tuple[str, ...] = (
    "cat",
    "find",
    "git branch",
    "git diff",
    "git log",
    "git ls-files",
    "git show",
    "git status",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "tail",
    "wc",
)

# `find` flags that turn it into something that writes, deletes, executes, or follows symlinks
# out of the worktree. `-fprint*` covers -fprint/-fprint0/-fprintf by prefix since find accepts
# all three.
# `-files0-from` reads a list of start paths from a file, so it can leak names from outside the
# worktree without naming them on the command line.
_FIND_MUTATING = {
    "-delete",
    "-exec",
    "-execdir",
    "-ok",
    "-okdir",
    "-fls",
    "-L",
    "-follow",
    "-files0-from",
}


def _find_mutates(rest: list[str]) -> bool:
    return any(arg in _FIND_MUTATING or arg.startswith("-fprint") for arg in rest)


# `git branch` is allowed for listing only. Every flag must be on this list (or a --format=/
# --sort= value); anything else — a combined short flag, an abbreviated long option, or a bare
# branch-name argument not preceded by --list/-l — creates, moves, or deletes a branch instead.
_GIT_BRANCH_ALLOWED_FLAGS = {
    "-a",
    "-r",
    "-v",
    "-vv",
    "--list",
    "--show-current",
    "--contains",
    "--no-contains",
    "--merged",
    "--no-merged",
    "--points-at",
    "--column",
    "--no-column",
    "--color",
    "--no-color",
    "-l",
}
_GIT_BRANCH_ALLOWED_PREFIXES = ("--format=", "--sort=")
_GIT_BRANCH_LIST_FLAGS = {"--list", "-l"}


def _git_branch_mutates(rest: list[str]) -> bool:
    seen_list = False
    for arg in rest:
        if arg in _GIT_BRANCH_ALLOWED_FLAGS:
            seen_list = seen_list or arg in _GIT_BRANCH_LIST_FLAGS
            continue
        if arg.startswith(_GIT_BRANCH_ALLOWED_PREFIXES):
            continue
        if arg.startswith("-") or not seen_list:
            return True
    return False


def _is_short_cluster(arg: str) -> bool:
    """Whether `arg` is a single-dash flag cluster: not a `--long` option, not a bare `-`."""
    return len(arg) > 1 and arg[0] == "-" and arg[1] != "-"


def _short_cluster_flags(arg: str, value_letters: str = "") -> str:
    """The letters of a single-dash cluster that are actually parsed as flags — stopping at (and
    including) the first letter in `value_letters`, since a value-taking flag's own attached
    value is not more flags (`-eR`'s "R" is `-e`'s pattern, not a `-R` flag). Returns "" if `arg`
    isn't a short cluster at all."""
    if not _is_short_cluster(arg):
        return ""
    body = arg[1:]
    for i, char in enumerate(body):
        if char in value_letters:
            return body[: i + 1]
    return body


# `rg --pre`/`--pre-glob` run an arbitrary preprocessor command on every searched file;
# `--hostname-bin` (paired with `--hyperlink-format`) does too. `-L`/`--follow` walk symlinks out
# of the worktree. Value-taking short flags (rg --help): e f g m A B C t T M j r E d — scanning
# stops at the first one, so its attached value (e.g. `-eL`'s "L") isn't mistaken for `-L`.
_RG_VALUE_LETTERS = "efgmABCtTMjrEd"
_RG_MUTATING_EXACT = {"--pre", "--pre-glob", "--hostname-bin", "--follow"}
_RG_MUTATING_PREFIXES = ("--pre=", "--pre-glob=", "--hostname-bin=")


def _rg_mutates(rest: list[str]) -> bool:
    return any(
        arg in _RG_MUTATING_EXACT
        or arg.startswith(_RG_MUTATING_PREFIXES)
        or "L" in _short_cluster_flags(arg, _RG_VALUE_LETTERS)
        for arg in rest
    )


# `grep -R`/`--dereference-recursive` walks symlinks out of the worktree; lowercase `-r` doesn't.
# BSD grep's `-S` does the same while recursing; `-O`/`-p` are refused alongside it per the
# reviewer. Value-taking short flags: e f m A B C d D.
_GREP_VALUE_LETTERS = "efmABCdD"
_GREP_MUTATING_EXACT = {"--dereference-recursive"}
_GREP_MUTATING_LETTERS = "RSOp"


def _grep_mutates(rest: list[str]) -> bool:
    return any(
        arg in _GREP_MUTATING_EXACT
        or any(char in _GREP_MUTATING_LETTERS for char in _short_cluster_flags(arg, _GREP_VALUE_LETTERS))
        for arg in rest
    )


# `ls -L`/`--dereference` shows (and so can be tricked into reading through) a symlink's target.
_LS_MUTATING_EXACT = {"--dereference"}


def _ls_mutates(rest: list[str]) -> bool:
    return any(arg in _LS_MUTATING_EXACT or "L" in _short_cluster_flags(arg) for arg in rest)


_GENERIC_FORBIDDEN_DETAIL = "uses shell operators or risky flags"
CONTAINMENT_DETAIL = "stay inside the worktree"


def _read_only_key(argv: list[str]) -> tuple[str, ...] | None:
    """The matched `READ_ONLY` entry's tokens, or None if `argv` doesn't match any of them."""
    for entry in READ_ONLY:
        tokens = tuple(entry.split())
        if len(argv) >= len(tokens) and tuple(argv[: len(tokens)]) == tokens:
            return tokens
    return None


# `wc --files0-from` reads the files to count from a list in another file, so it can leak counts
# (and names) from outside the worktree. GNU wc accepts any unambiguous prefix of a long option,
# and `--f` is already unique to --files0-from.
_WC_FILES0_FROM = "--files0-from"


def _wc_mutates(rest: list[str]) -> bool:
    for arg in rest:
        name = arg.split("=", 1)[0]
        if len(name) > 2 and _WC_FILES0_FROM.startswith(name):
            return True
    return False


def _read_only_mutates(argv: list[str], key: tuple[str, ...]) -> bool:
    rest = argv[len(key) :]
    if key == ("find",):
        return _find_mutates(rest)
    if key == ("git", "branch"):
        return _git_branch_mutates(rest)
    if key == ("rg",):
        return _rg_mutates(rest)
    if key == ("grep",):
        return _grep_mutates(rest)
    if key == ("ls",):
        return _ls_mutates(rest)
    if key == ("wc",):
        return _wc_mutates(rest)
    return False


@dataclass(frozen=True)
class _PatternGrammar:
    """How grep/rg split their arguments into option values, the pattern, and paths.

    `value_longs` take a value, which may be the next token when given without `=` — that token
    is consumed, never picked as the pattern, and (like every non-pattern token) containment-
    checked; this is what keeps a file-naming value (`--ignore-file`, `--exclude-from`, `--file`)
    from being skipped as "the pattern". `unsure_longs` consume a separate value in some
    implementations but not others, so the pattern can't be located and none is excluded.
    `pattern_less_longs` switch to a mode with no pattern, where every positional is a path.
    `abbreviations`: getopt_long accepts any unambiguous prefix of a long option, so a token that
    only prefixes a known value-taking option is never trusted either."""

    value_letters: str
    value_longs: frozenset[str]
    unsure_longs: frozenset[str] = frozenset()
    pattern_less_longs: frozenset[str] = frozenset()
    abbreviations: bool = False


# Union of GNU grep (Linux) and BSD grep (macOS's /usr/bin/grep). --exclude-from is GNU-only;
# --include-dir BSD-only. --context takes a separate value in GNU but only `=value` in BSD.
# --color/--colour take their optional value only via `=` in both, so they're plain flags here.
_GREP_GRAMMAR = _PatternGrammar(
    value_letters=_GREP_VALUE_LETTERS,
    value_longs=frozenset(
        {
            "--regexp",
            "--file",
            "--exclude-from",
            "--after-context",
            "--before-context",
            "--max-count",
            "--binary-files",
            "--devices",
            "--directories",
            "--exclude",
            "--exclude-dir",
            "--include",
            "--include-dir",
            "--label",
            "--group-separator",
        }
    ),
    unsure_longs=frozenset({"--context"}),
    abbreviations=True,
)

# `rg -h` (15.2): every long option shown with `=VALUE`. rg rejects abbreviated long options.
_RG_GRAMMAR = _PatternGrammar(
    value_letters=_RG_VALUE_LETTERS,
    value_longs=frozenset(
        {
            "--regexp",
            "--file",
            "--ignore-file",
            "--pre",
            "--pre-glob",
            "--dfa-size-limit",
            "--encoding",
            "--engine",
            "--max-count",
            "--regex-size-limit",
            "--threads",
            "--glob",
            "--iglob",
            "--max-depth",
            "--max-filesize",
            "--type",
            "--type-not",
            "--type-add",
            "--type-clear",
            "--after-context",
            "--before-context",
            "--color",
            "--colors",
            "--context",
            "--context-separator",
            "--field-context-separator",
            "--field-match-separator",
            "--hostname-bin",
            "--hyperlink-format",
            "--max-columns",
            "--path-separator",
            "--replace",
            "--sort",
            "--sortr",
            "--generate",
        }
    ),
    pattern_less_longs=frozenset({"--files"}),
)

_PATTERN_GRAMMARS = {("grep",): _GREP_GRAMMAR, ("rg",): _RG_GRAMMAR}
_PATTERN_LONGS = ("--regexp", "--file")


def _pattern_index_to_exclude(key: tuple[str, ...], rest: list[str]) -> int | None:
    """The index within `rest` of the grep/rg search pattern, when it's given positionally — a
    regex like "/api/" can look like a path without being one, so it's excluded from path
    containment. None (exclude nothing, check every token) whenever the pattern can't be located
    with certainty: -e/-f given anywhere (then every positional is a path), a pattern-less mode,
    or an option whose value-taking differs between implementations or is abbreviated."""
    grammar = _PATTERN_GRAMMARS.get(key)
    if grammar is None:
        return None
    letters = grammar.value_letters
    known_longs = grammar.value_longs | grammar.unsure_longs
    first_positional: int | None = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            if first_positional is None and i + 1 < len(rest):
                first_positional = i + 1
            break
        if arg.startswith("--"):
            name, has_value = arg.split("=", 1)[0], "=" in arg
            if name in _PATTERN_LONGS or name in grammar.pattern_less_longs:
                return None
            if not has_value and name in grammar.unsure_longs:
                return None
            if name not in known_longs and grammar.abbreviations:
                if any(option.startswith(name) for option in (*known_longs, *_PATTERN_LONGS)):
                    return None
            if not has_value and name in grammar.value_longs:
                i += 2
                continue
        elif _is_short_cluster(arg):
            flags = _short_cluster_flags(arg, letters)
            if "e" in flags or "f" in flags:
                return None
            if flags[-1] in letters and len(flags) == len(arg) - 1:
                i += 2  # the value-taking flag ends the token, so its value is the next one
                continue
        elif first_positional is None:
            first_positional = i
        i += 1
    return first_positional


def _path_candidates(arg: str) -> list[str]:
    """Every path-like reading of one argument token: the token itself, always; plus, for a flag
    token (starts with `-`), the value after `=` (`--file=/etc/x`) or — for an attached
    single-dash short option (`-f/abs`, `-fevil`) — the remainder after the flag letter. Applied
    uniformly to every argument, including ones after `--`, so a positional filename that happens
    to start with `-` (`cat -- -evil`) is still checked via "the token itself"."""
    candidates = [arg]
    if not arg.startswith("-"):
        return candidates
    if "=" in arg:
        candidates.append(arg.split("=", 1)[1])
    elif len(arg) > 2 and arg[1] != "-":
        candidates.append(arg[2:])
    return candidates


def _worktree_entry_exists(candidate: str, root: Path) -> bool:
    """Whether something already exists at `candidate` under `root`, per `os.lstat` — true for a
    symlink even when its target is missing or a loop, since lstat never follows the final
    component."""
    try:
        os.lstat(root / candidate)
    except OSError:
        return False
    return True


def _needs_containment_check(candidate: str, root: Path) -> bool:
    return "/" in candidate or candidate == ".." or _worktree_entry_exists(candidate, root)


def _outside_root(arg: str, root: Path) -> bool:
    candidate = Path(arg)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    return not resolved.is_relative_to(root)


_SECRET_TOKENS = {"AUTH", "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "CREDENTIALS"}
_SECRET_SUFFIXES = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD")


def is_secret_name(name: str) -> bool:
    tokens = [token for token in re.split(r"[^A-Z0-9]+", name.upper()) if token]
    return any(token in _SECRET_TOKENS or token.endswith(_SECRET_SUFFIXES) for token in tokens)


# `keyring`'s own variable for pinning its backend. Agent-run commands get the null backend, so
# code they run can't read Phil's stored keys through `keyring` (stripping the env vars alone
# would leave that way round).
KEYRING_BACKEND_VAR = "PYTHON_KEYRING_BACKEND"
NULL_KEYRING_BACKEND = "keyring.backends.null.Keyring"


def child_env(environ: Mapping[str, str], pass_env: Iterable[str] = ()) -> dict[str, str]:
    allowed = set(pass_env)
    env = {name: value for name, value in environ.items() if name in allowed or not is_secret_name(name)}
    env[KEYRING_BACKEND_VAR] = NULL_KEYRING_BACKEND
    return env


def _matches_pattern(argv: list[str], pattern_tokens: list[str]) -> bool:
    if not pattern_tokens or argv[0] != pattern_tokens[0]:
        return False
    rest = argv[1:]
    pattern_rest = pattern_tokens[1:]
    if pattern_rest and pattern_rest[-1] == "*":
        fixed = pattern_rest[:-1]
        if len(rest) < len(fixed):
            return False
        return all(fnmatch.fnmatchcase(a, p) for a, p in zip(rest, fixed))
    if len(rest) != len(pattern_rest):
        return False
    return all(fnmatch.fnmatchcase(a, p) for a, p in zip(rest, pattern_rest))


def _is_denied(argv: list[str]) -> bool:
    if argv[0] not in _RISKY_PROGRAMS:
        return False
    return any(arg.startswith(_RISKY_PREFIXES) or arg in _RISKY_EXACT for arg in argv[1:])


class ShellPolicy:
    def __init__(
        self,
        allow: list[str],
        *,
        extra_allow: Iterable[str] = (),
        approved: Iterable[str] = (),
        root: Path | None = None,
    ) -> None:
        self.allow = allow
        # `extra_allow` (the plan's test/check commands) matches literal-escaped tokens plus a
        # real trailing "*", so trailing arguments are still allowed. `approved` (commands a human
        # approved after a denial) matches only the exact command — never widened with a wildcard,
        # or an approval of `rm build.log` would also cover `rm build.log -rf /`.
        self.extra_allow = tuple(extra_allow)
        self.approved = tuple(approved)
        self.root = root

    def _classify(self, command: str) -> tuple[str | None, str | None]:
        """(reason, detail): reason is None if allowed, "forbidden" if no approval could make it
        safe, or "not_allowed" if only off the allowlist. detail is set only when reason is
        "forbidden", and is the one-line reason why."""
        command = command.strip()
        if _FORBIDDEN & set(command):
            return "forbidden", _GENERIC_FORBIDDEN_DETAIL
        try:
            argv = shlex.split(command)
        except ValueError:
            return "forbidden", _GENERIC_FORBIDDEN_DETAIL
        if not argv or _is_denied(argv):
            return "forbidden", _GENERIC_FORBIDDEN_DETAIL
        key = _read_only_key(argv)
        if key is not None:
            if _read_only_mutates(argv, key):
                return "forbidden", _GENERIC_FORBIDDEN_DETAIL
            if self.root is not None:
                root = self.root.resolve()
                rest = argv[1:]
                pattern_index = _pattern_index_to_exclude(key, rest)
                for i, arg in enumerate(rest):
                    if i == pattern_index:
                        continue
                    for candidate in _path_candidates(arg):
                        if _needs_containment_check(candidate, root) and _outside_root(candidate, root):
                            return "forbidden", CONTAINMENT_DETAIL
            return None, None
        for pattern in self.approved:
            try:
                pattern_tokens = shlex.split(literal_pattern(pattern))
            except ValueError:
                continue
            if _matches_pattern(argv, pattern_tokens):
                return None, None
        for pattern in self.extra_allow:
            try:
                pattern_tokens = [*shlex.split(literal_pattern(pattern)), "*"]
            except ValueError:
                continue
            if _matches_pattern(argv, pattern_tokens):
                return None, None
        for pattern in self.allow:
            try:
                pattern_tokens = shlex.split(pattern)
            except ValueError:
                continue
            if _matches_pattern(argv, pattern_tokens):
                return None, None
        return "not_allowed", None

    def denial_reason(self, command: str) -> str | None:
        """None if allowed; "forbidden" if no approval could make it safe; "not_allowed" if only off the allowlist."""
        reason, _ = self._classify(command)
        return reason

    def refusal_detail(self, command: str) -> str | None:
        """One-line reason `command` is forbidden, or None if it isn't (including "not_allowed")."""
        reason, detail = self._classify(command)
        return detail if reason == "forbidden" else None

    def is_allowed(self, command: str) -> bool:
        return self.denial_reason(command) is None


@dataclass(frozen=True)
class ShellResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def kill_active_groups(sig: int = signal.SIGKILL) -> list[int]:
    killed: list[int] = []
    for group in list(_ACTIVE_GROUPS):
        try:
            os.killpg(group, sig)
            killed.append(group)
        except ProcessLookupError:
            pass
        _ACTIVE_GROUPS.discard(group)
    return killed


def run_command(
    command: str, cwd: Path, timeout_s: float, env: Mapping[str, str] | None = None
) -> ShellResult:
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    if not cwd.is_dir():
        return ShellResult(command, 2, "", f"working directory does not exist: {cwd}", False, elapsed())

    try:
        args = shlex.split(command)
    except ValueError as exc:
        return ShellResult(command, 2, "", str(exc), False, elapsed())

    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
            env=dict(env) if env is not None else child_env(os.environ),
        )
    except FileNotFoundError as exc:
        return ShellResult(command, 127, "", str(exc), False, elapsed())
    except PermissionError as exc:
        return ShellResult(command, 126, "", str(exc), False, elapsed())
    except OSError as exc:
        return ShellResult(command, 126, "", str(exc), False, elapsed())

    _ACTIVE_GROUPS.add(proc.pid)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            return ShellResult(command, proc.returncode, stdout, stderr, False, elapsed())
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
            return ShellResult(command, -9, stdout, stderr, True, elapsed())
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
    finally:
        _ACTIVE_GROUPS.discard(proc.pid)


def truncate_output(
    text: str,
    max_lines: int,
    keep: tuple[str, ...] = ("FAIL", "Error", "error", "assert"),
) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head_n = max_lines // 4
    tail_n = max_lines // 2
    middle_budget = max_lines - head_n - tail_n
    middle = lines[head_n : len(lines) - tail_n]
    hits = [line for line in middle if any(marker in line for marker in keep)][:middle_budget]
    omitted = len(middle) - len(hits)
    marker = f"... [{omitted} lines omitted; full log saved] ..."
    return "\n".join([*lines[:head_n], marker, *hits, *lines[len(lines) - tail_n :]])
