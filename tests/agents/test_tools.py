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


def test_tool_is_named_and_documented(tmp_path):
    run_shell = make_shell_tool(tmp_path, shell_config(), CommandLog())
    assert run_shell.__name__ == "run_shell"
    assert run_shell.__doc__
