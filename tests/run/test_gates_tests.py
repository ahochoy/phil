import shlex
import sys

from phil.config import ShellConfig
from phil.run.gates import (
    MAX_FAILURES,
    couldnt_run_summary,
    first_output_line,
    is_test_path,
    parse_counts,
    parse_failures,
    run_tests,
)
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


def test_parse_counts():
    assert parse_counts("....\n3 passed, 1 failed, 2 skipped in 0.12s\n") == (3, 2)
    assert parse_counts("E   ImportError\n1 error in 0.1s\n") == (0, 0)
    assert parse_counts("\nno tests ran in 0.01s\n") == (0, 0)
    assert parse_counts("==== 1 failed, 4 passed, 1 warning in 2.50s ====") == (4, 0)
    assert parse_counts("npm ERR! missing script: test") == (None, None)
    assert parse_counts("") == (None, None)


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
    assert (report.passed_count, report.skipped_count) == (1, 0)
    assert (tmp_path / "run" / "logs" / "baseline.log").exists()
    assert not list(tmp_path.rglob("__pycache__"))


def test_new_failures_are_computed_before_truncation(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    old = [f"def test_old_{n:02d}():\n    assert False\n" for n in range(MAX_FAILURES + 5)]
    (tmp_path / "tests" / "test_a_old.py").write_text("\n\n".join(old))
    (tmp_path / "tests" / "test_z_new.py").write_text("def test_new():\n    assert False\n")
    baseline = [f"tests/test_a_old.py::test_old_{n:02d}" for n in range(MAX_FAILURES + 5)]
    report = run_tests(TEST_CMD, tmp_path, shell=ShellConfig(), artifacts=None, name="v", baseline=baseline)
    assert len(report.failures) == MAX_FAILURES
    assert report.new_failures_vs_baseline == ["tests/test_z_new.py::test_new"]


def test_run_tests_records_the_exit_code(tmp_path):
    make_project(tmp_path, "assert add(1, 2) == 4")
    failing = run_tests(TEST_CMD, tmp_path, shell=ShellConfig(), artifacts=None, name="v")
    assert failing.exit_code == 1
    missing = run_tests("definitely-not-a-program-xyz", tmp_path, shell=ShellConfig(), artifacts=None, name="v")
    assert missing.exit_code == 127


def test_first_output_line_skips_blank_and_npm_banner_lines():
    output = "\n> calc@1.0.0 test\n> jest\n\n  sh: jest: command not found  \nmore\n"
    assert first_output_line(output) == "sh: jest: command not found"
    assert first_output_line("> only a banner\n\n") == "command not found"
    assert first_output_line("") == "command not found"


def test_couldnt_run_summary_does_not_double_a_trailing_period():
    summary = couldnt_run_summary("npm test", "sh: npm: command not found.\n")
    assert ".." not in summary
    assert summary == (
        "`npm test` couldn't run: sh: npm: command not found. Dependencies may be missing in the "
        "run's worktree — set [project] setup_cmd (e.g. npm ci) — or the program isn't on PATH."
    )


def test_couldnt_run_summary_still_adds_a_period_with_none_to_strip():
    summary = couldnt_run_summary("npm test", "sh: npm: command not found\n")
    assert summary == (
        "`npm test` couldn't run: sh: npm: command not found. Dependencies may be missing in the "
        "run's worktree — set [project] setup_cmd (e.g. npm ci) — or the program isn't on PATH."
    )


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


def test_on_windows_run_tests_looks_up_the_configured_bash(tmp_path, monkeypatch):
    from phil import platform

    found = []
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(platform, "find_bash", lambda configured=None, **_: found.append(configured))
    report = run_tests(TEST_CMD, tmp_path, shell=ShellConfig(bash=r"D:\Git\bin\bash.exe"), artifacts=None, name="t")
    assert found == [r"D:\Git\bin\bash.exe"]
    assert not report.passed
