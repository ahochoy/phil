import shlex
import sys

import pytest

from phil.config import ShellConfig
from phil.workspace.shell import ShellPolicy, child_env, is_secret_name, literal_pattern, run_command, truncate_output

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
        ("pytestevil", False),
    ],
)
def test_policy(command, allowed):
    assert ShellPolicy(["pytest *", "pytest", "git status"]).is_allowed(command) is allowed


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("uv run bash -c 'rm -rf /'", False),
        ("git diff --output=/tmp/x", False),
        ("git diff --no-index a b", False),
        ("uv run pytest -q", True),
        ("git diff HEAD~1", True),
    ],
)
def test_policy_default_allowlist(command, allowed):
    assert ShellPolicy(ShellConfig().allow).is_allowed(command) is allowed


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("pytest -q", None),
        ("git push", "not_allowed"),
        ("pytest && echo hi", "forbidden"),
        ("python -c 'print(1)'", "forbidden"),
        ("pytest 'unterminated", "forbidden"),
    ],
)
def test_denial_reason(command, reason):
    policy = ShellPolicy(["pytest *", "pytest", "git status", "python *"])
    assert policy.denial_reason(command) == reason
    assert policy.is_allowed(command) is (reason is None)


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


def test_run_command_replaces_invalid_utf8(tmp_path):
    result = run_command(
        f"{PY} -c \"import sys; sys.stdout.buffer.write(b'\\xff\\xfe')\"",
        cwd=tmp_path,
        timeout_s=10,
    )
    assert result.ok
    assert result.exit_code == 0
    assert "�" in result.stdout


def test_run_command_missing_cwd(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = run_command(f"{PY} -c \"print('hi')\"", cwd=missing, timeout_s=10)
    assert result.exit_code == 2
    assert not result.ok
    assert "working directory does not exist" in result.stderr
    assert str(missing) in result.stderr


def test_run_command_non_executable_file(tmp_path):
    script = tmp_path / "not-executable.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)
    result = run_command(str(script), cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 126
    assert not result.ok


@pytest.mark.parametrize(
    "name",
    ["OPENROUTER_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "MY_APIKEY", "NPM_AUTH", "gh_token"],
)
def test_secret_names_are_detected(name):
    assert is_secret_name(name)


@pytest.mark.parametrize("name", ["PATH", "HOME", "GIT_AUTHOR_NAME", "KEYBOARD_LAYOUT", "LANG", "VIRTUAL_ENV"])
def test_ordinary_names_are_kept(name):
    assert not is_secret_name(name)


def test_child_env_strips_secrets_but_honours_pass_env():
    environ = {"PATH": "/bin", "OPENROUTER_API_KEY": "sk-1", "DATABASE_TOKEN": "t"}
    assert child_env(environ) == {"PATH": "/bin"}
    assert child_env(environ, pass_env=["DATABASE_TOKEN"]) == {"PATH": "/bin", "DATABASE_TOKEN": "t"}


def test_run_command_hides_secrets_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sk-should-not-leak")
    script = tmp_path / "show_env.py"
    script.write_text("import os\nprint(os.environ.get('FAKE_API_KEY', 'absent'))\n")
    result = run_command(f"{PY} {shlex.quote(str(script))}", cwd=tmp_path, timeout_s=10)
    assert result.stdout.strip() == "absent"


def test_run_command_uses_explicit_env(tmp_path):
    script = tmp_path / "show_env.py"
    script.write_text("import os\nprint(os.environ.get('ONLY_THIS', 'absent'))\n")
    result = run_command(f"{PY} {shlex.quote(str(script))}", cwd=tmp_path, timeout_s=10, env={"ONLY_THIS": "yes"})
    assert result.stdout.strip() == "yes"


def test_literal_pattern_matches_only_the_exact_command():
    exact = "pytest tests/test_foo.py::test_bar[case1]"
    policy = ShellPolicy([literal_pattern(exact), literal_pattern("pytest tests/*")])
    assert policy.is_allowed(exact)
    assert not policy.is_allowed("pytest tests/test_foo.py::test_bar[XYZ9]")
    assert policy.is_allowed("pytest tests/*")
    assert not policy.is_allowed("pytest tests/anything.py")
