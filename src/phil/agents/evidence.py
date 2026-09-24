import shlex
from pathlib import Path, PurePosixPath

from phil.contracts import Contract


def _tokens(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return (command.strip(),)


def check_evidence(output: Contract, *, commands: list[str], workdir: Path | None) -> list[str]:
    problems: list[str] = []
    ran = {_tokens(command) for command in commands}
    self_check = getattr(output, "self_check", None)
    if self_check is not None:
        for claim in self_check.evidence:
            if claim.command and _tokens(claim.command) not in ran:
                problems.append(f"claimed command was never run: {claim.command}")
    if workdir is not None:
        resolved_workdir = workdir.resolve()
        for path in getattr(output, "tests_added", []):
            pure_path = PurePosixPath(path)
            resolved_path = (workdir / path).resolve()
            outside = (
                pure_path.is_absolute()
                or ".." in pure_path.parts
                or not resolved_path.is_relative_to(resolved_workdir)
            )
            if outside:
                problems.append(f"tests_added path is outside the worktree: {path}")
            elif not resolved_path.exists():
                problems.append(f"tests_added file does not exist: {path}")
    return problems
