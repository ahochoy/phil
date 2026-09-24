import fnmatch
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_FORBIDDEN = set(";&|$`<>\n")


class ShellPolicy:
    def __init__(self, allow: list[str]) -> None:
        self.allow = allow

    def is_allowed(self, command: str) -> bool:
        command = command.strip()
        if _FORBIDDEN & set(command):
            return False
        return any(fnmatch.fnmatchcase(command, pattern) for pattern in self.allow)


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

    try:
        proc = subprocess.Popen(
            shlex.split(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return ShellResult(command, 127, "", str(exc), False, elapsed())
    except ValueError as exc:
        return ShellResult(command, 2, "", str(exc), False, elapsed())
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
