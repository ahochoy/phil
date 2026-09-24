import shlex
import sys

from phil.config import ShellConfig
from phil.run.gates import is_test_path, parse_failures, run_tests
from phil.store.artifacts import ArtifactStore

TEST_CMD = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"
GLOBS = ["tests/*", "test/*", "test_*.py", "*_test.py"]


def test_is_test_path_matches_full_path_or_name():
    assert is_test_path("tests/test_calc.py", GLOBS)
    assert is_test_path("pkg/test_util.py", GLOBS)
    assert is_test_path("tests/unit/helpers.py", GLOBS)
    assert not is_test_path("calc.py", GLOBS)
    assert not is_test_path("src/testing.py", GLOBS)


def test_parse_failures():
    output = (
        "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
        "ERROR tests/test_b.py - ImportError: cannot import name 'subtract'\n"
        "FAILED tests/test_a.py::test_x - duplicate line\n"
    )
    assert parse_failures(output, 1) == ["tests/test_a.py::test_x", "tests/test_b.py"]
    assert parse_failures("", 0) == []
    assert parse_failures("no tests ran", 5) == ["exit code 5"]
    assert parse_failures("", -9, timed_out=True) == ["timed out"]


def make_project(root, test_body):
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text(f"from calc import add\n\n\ndef test_add():\n    {test_body}\n")


def test_run_tests_passing(tmp_path):
    make_project(tmp_path, "assert add(1, 2) == 3")
    artifacts = ArtifactStore(tmp_path / "run")
    report = run_tests(TEST_CMD, tmp_path, shell=ShellConfig(), artifacts=artifacts, name="baseline")
    assert report.passed
    assert report.failures == []
    assert (tmp_path / "run" / "logs" / "baseline.log").exists()
    assert not list(tmp_path.rglob("__pycache__"))


def test_run_tests_failing_with_baseline(tmp_path):
    make_project(tmp_path, "assert add(1, 2) == 4")
    report = run_tests(
        TEST_CMD, tmp_path, shell=ShellConfig(), artifacts=None, name="v",
        baseline=["tests/test_calc.py::test_add"],
    )
    assert not report.passed
    assert report.failures == ["tests/test_calc.py::test_add"]
    assert report.new_failures_vs_baseline == []
    assert report.log_path == ""
