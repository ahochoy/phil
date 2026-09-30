from phil.config import ProjectConfig, ShellConfig
from phil.contracts import TestReport
from phil.run.gates import is_test_path, run_check, snapshot_tests, verify_check, verify_green, verify_red
from phil.workspace.shell import ShellResult

GLOBS = ["tests/*", "test_*.py"]
DEFAULT_GLOBS = ProjectConfig().test_globs


def report(new=(), failures=(), passed=None, skipped=None) -> TestReport:
    return TestReport(
        command="pytest", passed=not failures, failures=list(failures), log_path="", new_failures_vs_baseline=list(new),
        passed_count=passed, skipped_count=skipped,
    )


def test_red_passes_with_new_failing_tests_only():
    assert verify_red(["tests/test_sub.py"], report(new=["tests/test_sub.py"], failures=["tests/test_sub.py"]), GLOBS) == []


def test_red_rejects_product_changes_missing_tests_and_passing_tests():
    problems = verify_red(["calc.py"], report(), GLOBS)
    assert problems == [
        "red phase changed non-test files: calc.py",
        "red phase added or changed no test files",
        "no new failing tests compared with the baseline; red phase needs tests that fail",
    ]


def test_green_passes_when_tests_pass_and_are_untouched():
    snap = {"tests/test_sub.py": "abc"}
    assert verify_green(report(), snap, dict(snap)) == []


def test_green_rejects_failures_and_test_edits():
    problems = verify_green(
        report(new=["tests/test_sub.py::test_subtract"], failures=["tests/test_sub.py::test_subtract"]),
        {"tests/test_sub.py": "abc"},
        {"tests/test_sub.py": "def", "tests/test_new.py": "123"},
    )
    assert problems == [
        "tests still failing: tests/test_sub.py::test_subtract",
        "green phase modified test files: tests/test_new.py, tests/test_sub.py",
    ]


def test_snapshot_hashes_test_files_and_marks_deletions(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x")
    snap = snapshot_tests(tmp_path, ["tests/test_a.py", "tests/test_gone.py", "calc.py"], GLOBS)
    assert set(snap) == {"tests/test_a.py", "tests/test_gone.py"}
    assert len(snap["tests/test_a.py"]) == 64
    assert snap["tests/test_gone.py"] == "<deleted>"


def test_test_config_files_count_as_test_paths():
    for path in ["conftest.py", "tests/conftest.py", "pkg/conftest.py", "pytest.ini", "tox.ini", "jest.config.js"]:
        assert is_test_path(path, DEFAULT_GLOBS), path
    for path in ["pyproject.toml", "setup.cfg", "calc.py"]:
        assert not is_test_path(path, DEFAULT_GLOBS), path


def test_green_rejects_a_new_collection_hook(tmp_path):
    (tmp_path / "conftest.py").write_text("def pytest_collection_modifyitems(items):\n    items.clear()\n")
    red = {"tests/test_sub.py": "abc"}
    now = {**red, **snapshot_tests(tmp_path, ["conftest.py"], DEFAULT_GLOBS)}
    assert verify_green(report(), red, now) == ["green phase modified test files: conftest.py"]


def test_red_may_add_a_conftest_fixture():
    changed = ["conftest.py", "tests/test_sub.py"]
    assert verify_red(changed, report(new=["tests/test_sub.py"], failures=["tests/test_sub.py"]), DEFAULT_GLOBS) == []


def red_report(passed, skipped):
    return report(new=["tests/test_sub.py"], failures=["tests/test_sub.py"], passed=passed, skipped=skipped)


def test_red_rejects_fewer_passing_tests():
    problems = verify_red(["tests/test_sub.py"], red_report(2, 0), GLOBS, base_passed=3, base_skipped=0)
    assert problems == ["red phase reduced passing tests from 3 to 2"]


def test_red_rejects_more_skipped_tests():
    problems = verify_red(["tests/test_sub.py"], red_report(3, 1), GLOBS, base_passed=3, base_skipped=0)
    assert problems == ["red phase increased skipped tests from 0 to 1"]


def test_red_ignores_counts_when_unknown():
    assert verify_red(["tests/test_sub.py"], red_report(None, None), GLOBS, base_passed=3, base_skipped=0) == []
    assert verify_red(["tests/test_sub.py"], red_report(0, 5), GLOBS) == []


def test_green_rejects_no_new_passing_tests():
    snap = {"tests/test_sub.py": "abc"}
    problems = verify_green(report(passed=3, skipped=0), snap, dict(snap), base_passed=3, base_skipped=0)
    assert problems == ["green phase did not add passing tests (3 passing, 3 before the task)"]


def test_green_rejects_more_skipped_tests():
    snap = {"tests/test_sub.py": "abc"}
    problems = verify_green(report(passed=4, skipped=2), snap, dict(snap), base_passed=3, base_skipped=1)
    assert problems == ["green phase increased skipped tests from 1 to 2"]


def test_green_passes_with_more_passing_tests():
    snap = {"tests/test_sub.py": "abc"}
    assert verify_green(report(passed=4, skipped=0), snap, dict(snap), base_passed=3, base_skipped=0) == []


def check_result(exit_code=0, stdout="", stderr="", timed_out=False) -> ShellResult:
    return ShellResult(
        command="npm run build", exit_code=exit_code, stdout=stdout, stderr=stderr, timed_out=timed_out, duration_ms=1
    )


def test_check_passes_when_its_command_succeeds_and_tests_are_untouched():
    snap = {"tests/test_calc.py": "abc"}
    assert verify_check(report(passed=3), check_result(), snap, dict(snap), base_passed=3) == []


def test_check_reports_a_failing_exit_with_the_output_tail():
    output = "\n".join(f"line {n}" for n in range(1, 31))
    problems = verify_check(report(), check_result(exit_code=2, stdout=output, stderr="boom"), {}, {})
    assert len(problems) == 1
    head, *tail = problems[0].splitlines()
    assert head == "check command failed (exit 2): npm run build"
    assert tail == [*(f"line {n}" for n in range(12, 31)), "boom"]


def test_check_reports_a_timeout():
    problems = verify_check(report(), check_result(exit_code=-9, timed_out=True), {}, {})
    assert problems == ["check command failed (timed out): npm run build"]


def test_check_rejects_fewer_passing_tests():
    problems = verify_check(report(passed=2), check_result(), {}, {}, base_passed=3)
    assert problems == ["check task reduced passing tests from 3 to 2"]


def test_check_messages_name_the_check_task_not_the_green_phase():
    problems = verify_check(
        report(new=["tests/test_calc.py::test_add"], passed=3, skipped=2),
        check_result(),
        {"tests/test_calc.py": "abc"},
        {"tests/test_calc.py": "def"},
        base_passed=3,
        base_skipped=1,
    )
    assert problems == [
        "tests still failing: tests/test_calc.py::test_add",
        "check task modified test files: tests/test_calc.py",
        "check task increased skipped tests from 1 to 2",
    ]
    assert not any("green" in problem for problem in problems)


def test_run_check_refuses_a_forbidden_command_without_running_it(tmp_path):
    marker = tmp_path / "ran"
    result = run_check(f"touch {marker}; true", tmp_path, shell=ShellConfig(), artifacts=None, name="check")
    assert not result.ok
    assert not marker.exists()
    assert "refused" in result.stderr
