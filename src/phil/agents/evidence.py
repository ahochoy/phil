import re
import shlex
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from phil.contracts import Contract

# The shell tool's own name always counts as a shell call, so a claim naming it must still match
# by command tokens rather than being waved through just because "run_shell" was called.
_SHELL_TOOL_NAME = "run_shell"


# Models write a claim's command loosely: "run_shell npm test", "a; b", "cat x (after the build)".
_TRAILING_NOTE = re.compile(r"\s+\([^()]*\)\s*$")
_JOINERS = re.compile(r"\s*(?:;|&&)\s*")


def _tokens(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return (command.strip(),)


def _parts(command: str) -> list[tuple[str, ...]]:
    """The commands a loosely written claim names: a trailing `(note)` dropped, split on `;` and
    `&&`, and a leading `run_shell` dropped from each part."""
    parts = []
    for part in _JOINERS.split(_TRAILING_NOTE.sub("", command)):
        tokens = _tokens(part)
        if tokens[:1] == (_SHELL_TOOL_NAME,):
            tokens = tokens[1:]
        parts.append(tokens)
    return parts


def check_evidence(
    output: Contract, *, commands: list[str], workdir: Path | None, tools: Iterable[str] = ()
) -> list[str]:
    problems: list[str] = []
    ran = {_tokens(command) for command in commands}
    tool_names = set(tools) - {_SHELL_TOOL_NAME}
    self_check = getattr(output, "self_check", None)
    if self_check is not None:
        for claim in self_check.evidence:
            if not claim.command:
                continue
            tokens = _tokens(claim.command)
            if tokens in ran:
                continue
            if tokens and tokens[0] in tool_names:
                continue
            parts = _parts(claim.command)
            if all(part and (part in ran or part[0] in tool_names) for part in parts):
                continue
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
