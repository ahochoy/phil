import fnmatch
import os
import re
from pathlib import Path, PurePosixPath

from phil.config import ShellConfig
from phil.contracts import TestReport
from phil.store.artifacts import ArtifactStore
from phil.workspace.shell import child_env, run_command

_FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
MAX_FAILURES = 50


def is_test_path(path: str, globs: list[str]) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatchcase(path, glob) or fnmatch.fnmatchcase(name, glob) for glob in globs)


def parse_failures(output: str, exit_code: int, timed_out: bool = False) -> list[str]:
    if timed_out:
        return ["timed out"]
    ids = list(dict.fromkeys(_FAILURE_LINE.findall(output)))
    if exit_code != 0 and not ids:
        return [f"exit code {exit_code}"]
    return ids


def run_tests(
    test_cmd: str,
    worktree: Path,
    *,
    shell: ShellConfig,
    artifacts: ArtifactStore | None,
    name: str,
    baseline: list[str] = (),
) -> TestReport:
    env = child_env(os.environ, shell.pass_env) | {"PYTHONDONTWRITEBYTECODE": "1"}
    result = run_command(test_cmd, worktree, shell.timeout_s, env=env)
    output = result.stdout + (f"\n{result.stderr}" if result.stderr else "")
    failures = parse_failures(output, result.exit_code, result.timed_out)[:MAX_FAILURES]
    log_path = str(artifacts.write_log(name, output)) if artifacts is not None else ""
    known = set(baseline)
    return TestReport(
        command=test_cmd,
        passed=result.ok,
        failures=failures,
        log_path=log_path,
        new_failures_vs_baseline=[failure for failure in failures if failure not in known],
    )
