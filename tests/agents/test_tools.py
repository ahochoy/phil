import shlex
import sys

from phil.agents.tools import CommandLog, make_shell_tool
from phil.config import ShellConfig
from phil.store.artifacts import ArtifactStore

PY = shlex.quote(sys.executable)


def shell_config(**overrides) -> ShellConfig:
    return ShellConfig(**({"allow": [f"{sys.executable} *"], "timeout_s": 10} | overrides))


def test_allowed_command_runs_and_is_logged(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    log = CommandLog()
    run_shell = make_shell_tool(tmp_path, shell_config(), log)
    output = run_shell(f"{PY} hello.py")
    assert output.startswith("exit_code: 0")
    assert "hi" in output
    assert log.commands == [f"{PY} hello.py"]


def test_denied_command_is_not_run_or_logged(tmp_path):
    log = CommandLog()
    run_shell = make_shell_tool(tmp_path, shell_config(), log)
    output = run_shell("rm -rf /")
    assert output.startswith("DENIED:")
    assert log.commands == []


def test_forbidden_command_is_refused_not_denied(tmp_path):
    log = CommandLog()
    run_shell = make_shell_tool(tmp_path, shell_config(), log)
    command = f"{PY} hello.py && echo hi"
    output = run_shell(command)
    assert output == (
        f"REFUSED: `{command}` uses shell operators or risky flags and can never run here. "
        "Use a plain allowlisted command instead."
    )
    assert (log.commands, log.denied, log.refused) == ([], [], [command])


def test_long_output_is_truncated_and_saved(tmp_path):
    (tmp_path / "spam.py").write_text("for i in range(1000):\n    print(i)\n")
    artifacts = ArtifactStore(tmp_path / "run")
    run_shell = make_shell_tool(tmp_path, shell_config(max_output_lines=20), CommandLog(), artifacts)
    output = run_shell(f"{PY} spam.py")
    assert "lines omitted" in output
    assert "full log: " in output
    assert (tmp_path / "run" / "logs" / "shell-1.log").read_text().count("\n") >= 1000


def test_secrets_are_not_visible_to_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sk-should-not-leak")
    (tmp_path / "env.py").write_text("import os\nprint(os.environ.get('FAKE_API_KEY', 'absent'))\n")
    output = make_shell_tool(tmp_path, shell_config(), CommandLog())(f"{PY} env.py")
    assert "absent" in output
    assert "sk-should-not-leak" not in output


def test_pass_env_lets_named_variables_through(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "needed")
    (tmp_path / "env.py").write_text("import os\nprint(os.environ.get('SERVICE_TOKEN', 'absent'))\n")
    output = make_shell_tool(tmp_path, shell_config(pass_env=["SERVICE_TOKEN"]), CommandLog())(f"{PY} env.py")
    assert "needed" in output


def test_commands_write_no_bytecode(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    (tmp_path / "helper.py").write_text("VALUE = 1\n")
    (tmp_path / "main.py").write_text("import helper\nprint(helper.VALUE)\n")
    output = make_shell_tool(tmp_path, shell_config(), CommandLog())(f"{PY} main.py")
    assert output.startswith("exit_code: 0")
    assert not list(tmp_path.rglob("__pycache__"))


def test_tool_is_named_and_documented(tmp_path):
    run_shell = make_shell_tool(tmp_path, shell_config(), CommandLog())
    assert run_shell.__name__ == "run_shell"
    assert run_shell.__doc__


def test_log_prefix_keeps_two_runs_from_overwriting_each_other(tmp_path):
    (tmp_path / "a.py").write_text("print('a')\n")
    (tmp_path / "b.py").write_text("print('b')\n")
    artifacts = ArtifactStore(tmp_path / "run")
    run_a = make_shell_tool(tmp_path, shell_config(), CommandLog(), artifacts, log_prefix="implement-T1-1")
    run_b = make_shell_tool(tmp_path, shell_config(), CommandLog(), artifacts, log_prefix="implement-T2-1")
    run_a(f"{PY} a.py")
    run_b(f"{PY} b.py")
    assert (tmp_path / "run" / "logs" / "implement-T1-1-shell-1.log").read_text().strip() == "a"
    assert (tmp_path / "run" / "logs" / "implement-T2-1-shell-1.log").read_text().strip() == "b"


def test_default_log_prefix_is_unchanged(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    artifacts = ArtifactStore(tmp_path / "run")
    run_shell = make_shell_tool(tmp_path, shell_config(), CommandLog(), artifacts)
    run_shell(f"{PY} hello.py")
    assert (tmp_path / "run" / "logs" / "shell-1.log").exists()
