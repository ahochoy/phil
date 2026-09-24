import shlex
import sys

import pytest

from phil.workspace.shell import ShellPolicy, run_command, truncate_output

PY = shlex.quote(sys.executable)


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("pytest -q", True),
        ("git status", True),
        ("git push", False),
        ("pytest; rm -rf /", False),
        ("pytest && echo hi", False),
        ("pytest | tee out", False),
        ("echo $(whoami)", False),
        ("pytest > out.txt", False),
    ],
)
def test_policy(command, allowed):
    assert ShellPolicy(["pytest*", "git status"]).is_allowed(command) is allowed


def test_run_command_captures_output(tmp_path):
    result = run_command(f"{PY} -c \"print('hi')\"", cwd=tmp_path, timeout_s=10)
    assert result.ok
    assert result.stdout.strip() == "hi"
    assert result.exit_code == 0


def test_run_command_nonzero_exit(tmp_path):
    result = run_command(f'{PY} -c "import sys; sys.exit(3)"', cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 3
    assert not result.ok


def test_run_command_missing_program(tmp_path):
    result = run_command("definitely-not-a-real-program-xyz", cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 127
    assert not result.ok


def test_run_command_times_out(tmp_path):
    result = run_command(f'{PY} -c "import time; time.sleep(10)"', cwd=tmp_path, timeout_s=0.5)
    assert result.timed_out
    assert not result.ok
    assert result.duration_ms < 5000


def test_truncate_short_text_unchanged():
    assert truncate_output("a\nb\nc", max_lines=10) == "a\nb\nc"


def test_truncate_keeps_head_tail_and_failures():
    lines = [f"line {i}" for i in range(1000)]
    lines[500] = "FAILED tests/test_map.py::test_markers"
    out = truncate_output("\n".join(lines), max_lines=40).splitlines()
    assert out[0] == "line 0"
    assert out[-1] == "line 999"
    assert "FAILED tests/test_map.py::test_markers" in out
    assert any("lines omitted" in line for line in out)
    assert len(out) <= 41


def test_run_command_malformed_quoting(tmp_path):
    result = run_command('pytest "unterminated', cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 2
    assert not result.ok
    assert "quotation" in result.stderr
