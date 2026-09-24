import fnmatch
import hashlib
import os
import re
from pathlib import Path, PurePosixPath

from phil.config import ShellConfig
from phil.contracts import TestReport
from phil.store.artifacts import ArtifactStore
from phil.workspace.shell import child_env, run_command

_FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_SUMMARY_LINE = re.compile(r"^=*\s*(no tests ran|\d+ [a-z]+(?:, \d+ [a-z]+)*) in [\d.]+s\b", re.MULTILINE)
_SUMMARY_COUNT = re.compile(r"(\d+) ([a-z]+)")
MAX_FAILURES = 50
# pytest aborts the whole session on a collection error unless told to continue; other runners ignore this.
_CONTINUE = "--continue-on-collection-errors"


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


def parse_counts(output: str) -> tuple[int | None, int | None]:
    """Passed and skipped counts from pytest's final summary line, or (None, None) without one."""
    summaries = _SUMMARY_LINE.findall(output)
    if not summaries:
        return None, None
    counts = {word: int(number) for number, word in _SUMMARY_COUNT.findall(summaries[-1])}
    return counts.get("passed", 0), counts.get("skipped", 0)


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
    addopts = env.get("PYTEST_ADDOPTS")
    env["PYTEST_ADDOPTS"] = f"{addopts} {_CONTINUE}" if addopts else _CONTINUE
    result = run_command(test_cmd, worktree, shell.timeout_s, env=env)
    output = result.stdout + (f"\n{result.stderr}" if result.stderr else "")
    all_failures = parse_failures(output, result.exit_code, result.timed_out)
    log_path = str(artifacts.write_log(name, output)) if artifacts is not None else ""
    known = set(baseline)
    passed_count, skipped_count = parse_counts(output)
    return TestReport(
        command=test_cmd,
        passed=result.ok,
        failures=all_failures[:MAX_FAILURES],
        log_path=log_path,
        new_failures_vs_baseline=[failure for failure in all_failures if failure not in known],
        passed_count=passed_count,
        skipped_count=skipped_count,
    )


DELETED = "<deleted>"


def snapshot_tests(worktree: Path, changed: list[str], globs: list[str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in changed:
        if not is_test_path(path, globs):
            continue
        target = worktree / path
        snapshot[path] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else DELETED
    return snapshot


def _skip_problem(phase: str, report: TestReport, base_skipped: int | None) -> list[str]:
    if report.skipped_count is None or base_skipped is None or report.skipped_count <= base_skipped:
        return []
    return [f"{phase} phase increased skipped tests from {base_skipped} to {report.skipped_count}"]


def verify_red(
    changed: list[str],
    report: TestReport,
    globs: list[str],
    base_passed: int | None = None,
    base_skipped: int | None = None,
) -> list[str]:
    problems: list[str] = []
    non_test = [path for path in changed if not is_test_path(path, globs)]
    if non_test:
        problems.append(f"red phase changed non-test files: {', '.join(non_test)}")
    if not any(is_test_path(path, globs) for path in changed):
        problems.append("red phase added or changed no test files")
    if not report.new_failures_vs_baseline:
        problems.append("no new failing tests compared with the baseline; red phase needs tests that fail")
    if report.passed_count is not None and base_passed is not None and report.passed_count < base_passed:
        problems.append(f"red phase reduced passing tests from {base_passed} to {report.passed_count}")
    problems += _skip_problem("red", report, base_skipped)
    return problems


def verify_green(
    report: TestReport,
    red_snapshot: dict[str, str],
    now_snapshot: dict[str, str],
    base_passed: int | None = None,
    base_skipped: int | None = None,
) -> list[str]:
    problems: list[str] = []
    if report.new_failures_vs_baseline:
        problems.append(f"tests still failing: {', '.join(report.new_failures_vs_baseline)}")
    edited = sorted(path for path in set(red_snapshot) | set(now_snapshot) if red_snapshot.get(path) != now_snapshot.get(path))
    if edited:
        problems.append(f"green phase modified test files: {', '.join(edited)}")
    if report.passed_count is not None and base_passed is not None and report.passed_count <= base_passed:
        problems.append(
            f"green phase did not add passing tests ({report.passed_count} passing, {base_passed} before the task)"
        )
    problems += _skip_problem("green", report, base_skipped)
    return problems
