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
_FIND_MUTATING = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-L", "-follow"}


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


# `rg --pre`/`--pre-glob` run an arbitrary preprocessor command on every searched file.
_RG_MUTATING_EXACT = {"--pre", "--pre-glob"}
_RG_MUTATING_PREFIXES = ("--pre=", "--pre-glob=")


def _rg_mutates(rest: list[str]) -> bool:
    return any(arg in _RG_MUTATING_EXACT or arg.startswith(_RG_MUTATING_PREFIXES) for arg in rest)


_GENERIC_FORBIDDEN_DETAIL = "uses shell operators or risky flags"
_CONTAINMENT_DETAIL = "stay inside the worktree"


def _read_only_key(argv: list[str]) -> tuple[str, ...] | None:
    """The matched `READ_ONLY` entry's tokens, or None if `argv` doesn't match any of them."""
    for entry in READ_ONLY:
        tokens = tuple(entry.split())
        if len(argv) >= len(tokens) and tuple(argv[: len(tokens)]) == tokens:
            return tokens
    return None


def _read_only_mutates(argv: list[str], key: tuple[str, ...]) -> bool:
    rest = argv[len(key) :]
    if key == ("find",):
        return _find_mutates(rest)
    if key == ("git", "branch"):
        return _git_branch_mutates(rest)
    if key == ("rg",):
        return _rg_mutates(rest)
    return False


def _looks_like_path(arg: str) -> bool:
    return not arg.startswith("-") and ("/" in arg or arg == "..")


def _flag_path_value(arg: str) -> str | None:
    """The path-like value carried by a flag argument, if any: the text after `=`, or — for an
    attached short option like `-f/etc/x` — the remainder after the option letter, when it
    contains a `/`. A separate argument (`-f`, `/etc/x`) is handled by the plain path check."""
    if not arg.startswith("-"):
        return None
    if "=" in arg:
        return arg.split("=", 1)[1]
    if len(arg) > 2 and arg[1] != "-" and "/" in arg[2:]:
        return arg[2:]
    return None


def _escapes_via_symlink(arg: str, root: Path) -> bool:
    """Whether a bare argument with no `/` and not `..` should still be containment-checked,
    because something exists at that name under `root` — most importantly a symlink whose real
    target lies outside it."""
    candidate = root / arg
    return candidate.exists() or candidate.is_symlink()


def _outside_root(arg: str, root: Path) -> bool:
    candidate = Path(arg)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    return not resolved.is_relative_to(root)


_SECRET_TOKENS = {"AUTH", "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "CREDENTIALS"}
_SECRET_SUFFIXES = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD")


def is_secret_name(name: str) -> bool:
    tokens = [token for token in re.split(r"[^A-Z0-9]+", name.upper()) if token]
    return any(token in _SECRET_TOKENS or token.endswith(_SECRET_SUFFIXES) for token in tokens)


def child_env(environ: Mapping[str, str], pass_env: Iterable[str] = ()) -> dict[str, str]:
    allowed = set(pass_env)
    return {name: value for name, value in environ.items() if name in allowed or not is_secret_name(name)}


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
                for arg in argv[1:]:
                    if arg.startswith("-"):
                        value = _flag_path_value(arg)
                        if value is not None and _outside_root(value, root):
                            return "forbidden", _CONTAINMENT_DETAIL
                        continue
                    if (_looks_like_path(arg) or _escapes_via_symlink(arg, root)) and _outside_root(arg, root):
                        return "forbidden", _CONTAINMENT_DETAIL
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
