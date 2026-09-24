import re
import subprocess
from pathlib import Path

RUN_ID_PATTERN = r"^r-[0-9a-f]{4}$"


class GitError(Exception):
    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.args = list(args)
        self.returncode = returncode
        self.stderr = stderr

    def __str__(self) -> str:
        return f"git {' '.join(self.args)} failed (exit {self.returncode}): {self.stderr}"


class GitNotFound(GitError):
    pass


def git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        if exc.filename == "git":
            raise GitNotFound(list(args), 127, str(exc)) from exc
        raise
    if proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc.stdout


def branch_for(run_id: str) -> str:
    if not re.fullmatch(RUN_ID_PATTERN, run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    return f"phil/{run_id}"
