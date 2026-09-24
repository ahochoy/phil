from phil.contracts import TestReport
from phil.run.gates import snapshot_tests, verify_green, verify_red

GLOBS = ["tests/*", "test_*.py"]


def report(new=(), failures=()) -> TestReport:
    return TestReport(
        command="pytest", passed=not failures, failures=list(failures), log_path="", new_failures_vs_baseline=list(new)
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
