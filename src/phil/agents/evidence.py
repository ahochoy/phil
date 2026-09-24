from pathlib import Path

from phil.contracts import Contract


def check_evidence(output: Contract, *, commands: list[str], workdir: Path | None) -> list[str]:
    problems: list[str] = []
    ran = {command.strip() for command in commands}
    self_check = getattr(output, "self_check", None)
    if self_check is not None:
        for claim in self_check.evidence:
            if claim.command and claim.command.strip() not in ran:
                problems.append(f"claimed command was never run: {claim.command}")
    if workdir is not None:
        for path in getattr(output, "tests_added", []):
            if not (workdir / path).exists():
                problems.append(f"tests_added file does not exist: {path}")
    return problems
