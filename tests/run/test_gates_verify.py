from phil.config import ProjectConfig
from phil.contracts import TestReport
from phil.run.gates import is_test_path, snapshot_tests, verify_green, verify_red

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
