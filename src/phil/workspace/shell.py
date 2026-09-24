import fnmatch
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_FORBIDDEN = set(";&|$`<>\n")

_RISKY_PROGRAMS = {"git", "npm", "uv", "python", "python3"}
_RISKY_PREFIXES = ("--output", "--no-index", "--ext-diff", "--prefix")
_RISKY_EXACT = {"-c", "-e"}


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
    def __init__(self, allow: list[str]) -> None:
        self.allow = allow

    def is_allowed(self, command: str) -> bool:
        command = command.strip()
        if _FORBIDDEN & set(command):
            return False
        try:
            argv = shlex.split(command)
        except ValueError:
            return False
        if not argv:
            return False
        matched = False
        for pattern in self.allow:
            try:
                pattern_tokens = shlex.split(pattern)
            except ValueError:
                continue
            if _matches_pattern(argv, pattern_tokens):
                matched = True
                break
        if not matched:
            return False
        return not _is_denied(argv)


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


def run_command(command: str, cwd: Path, timeout_s: float) -> ShellResult:
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
        )
    except FileNotFoundError as exc:
        return ShellResult(command, 127, "", str(exc), False, elapsed())
    except PermissionError as exc:
        return ShellResult(command, 126, "", str(exc), False, elapsed())
    except OSError as exc:
        return ShellResult(command, 126, "", str(exc), False, elapsed())
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
