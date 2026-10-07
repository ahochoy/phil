import pytest
from typer.testing import CliRunner

from phil import platform
from phil.cli import main as cli
from phil.ui.theme import make_console
from tests.run.conftest import calc_plan

runner = CliRunner()


@pytest.fixture
def windows_without_bash(monkeypatch):
    # Wide enough that the message never wraps, so it can be matched whole.
    monkeypatch.setattr(cli, "console", make_console(width=300))
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(platform, "find_bash", lambda configured=None, **_: None)


def test_phil_run_refuses_without_bash(calc_repo, tmp_path, monkeypatch, windows_without_bash):
    spawned = []
    monkeypatch.setattr(cli, "spawn_worker", lambda *a, **k: spawned.append(a))
    plan = tmp_path / "plan.json"
    plan.write_text(calc_plan().model_dump_json())
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), "run", str(plan)])
    assert result.exit_code != 0
    assert platform.MISSING_BASH in result.output
    assert spawned == []


@pytest.mark.parametrize("args", [[], ["setup"], ["models", "check"]])
def test_the_other_agent_commands_refuse_without_bash(calc_repo, windows_without_bash, args):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), *args])
    assert result.exit_code != 0
    assert platform.MISSING_BASH in result.output


@pytest.mark.parametrize("args", [["config"], ["keys", "list"], ["--help"]])
def test_config_keys_and_help_still_work_without_bash(calc_repo, windows_without_bash, args):
    result = runner.invoke(cli.app, ["--repo", str(calc_repo), *args])
    assert result.exit_code == 0, result.output
    assert platform.MISSING_BASH not in result.output
