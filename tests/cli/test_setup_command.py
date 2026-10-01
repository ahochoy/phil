from typer.testing import CliRunner

from phil.agents.check import CHECK_WORD, ModelCheck
from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.config import global_config_path, load_config
from phil.setup.io import ScriptedSetupIO
from phil.ui.theme import make_console

runner = CliRunner()
OK = ModelCheck(ok=True, echo=CHECK_WORD)
SECRET = "sk-TESTSECRET-123"


def use(monkeypatch, answers):
    io = ScriptedSetupIO(answers)
    monkeypatch.setattr(cli, "console", make_console(width=300))
    monkeypatch.setattr(cli, "_setup_io", lambda: io)
    monkeypatch.setattr(cli, "_factory_from_env", lambda: ScriptedAgentFactory({"model_check": [OK, OK]}))
    return io


def test_setup_writes_the_global_config_and_exits_0(git_repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    io = use(monkeypatch, ["OpenAI", "gpt-big", "gpt-small"])
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "setup"])
    assert result.exit_code == 0, result.output
    assert load_config(git_repo).models == {"high": "openai:gpt-big", "low": "openai:gpt-small"}
    assert any(line.startswith("✓ high  openai:gpt-big  ") for line in io.lines)
    assert any(line.startswith("✓ low  openai:gpt-small  ") for line in io.lines)
    assert SECRET not in global_config_path().read_text()


def test_setup_runs_outside_a_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    use(monkeypatch, ["OpenAI", "gpt-big", "gpt-small"])
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["setup"])
    assert result.exit_code == 0, result.output
    assert global_config_path().is_file()


def test_a_cancelled_setup_exits_1_and_writes_nothing(git_repo, monkeypatch):
    use(monkeypatch, ["OpenAI"])
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "setup"])
    assert result.exit_code == 1
    assert not global_config_path().exists()


def test_setup_uses_the_set_overrides(git_repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    io = use(monkeypatch, ["", "", ""])
    result = runner.invoke(
        cli.app, ["--repo", str(git_repo), "--set", "models.high=openai:gpt-now", "--set", "models.low=openai:gpt-lo", "setup"]
    )
    assert result.exit_code == 0, result.output
    assert ("ask", "high model") in io.prompts
    assert load_config(git_repo).models == {"high": "openai:gpt-now", "low": "openai:gpt-lo"}
