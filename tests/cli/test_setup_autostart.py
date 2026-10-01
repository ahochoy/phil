from typer.testing import CliRunner

from phil.cli import main as cli
from tests.helpers import MODELS_TOML
from tests.run.conftest import calc_plan

runner = CliRunner()


def _fake_run_setup_writes_models(io, *, config, check, catalog, ollama, write, path=None):
    from phil.config import global_config_path

    target = path or global_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(MODELS_TOML)
    return True


def test_autostart_runs_setup_and_continues_into_the_chat(git_repo, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("phil.setup.flow.run_setup", _fake_run_setup_writes_models)
    calls = []
    monkeypatch.setattr(cli, "_run_chat", lambda *args, **kwargs: calls.append(args))
    result = runner.invoke(cli.app, ["--repo", str(git_repo)])
    assert result.exit_code == 0, result.output
    assert "Phil isn't set up yet. Let's choose your models (about a minute)." in result.output
    assert len(calls) == 1


def test_autostart_cancelled_exits_1_with_the_missing_model_messages(git_repo, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("phil.setup.flow.run_setup", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(cli, "_run_chat", lambda *args, **kwargs: calls.append(args))
    result = runner.invoke(cli.app, ["--repo", str(git_repo)])
    assert result.exit_code == 1
    assert "Phil isn't set up yet. Let's choose your models (about a minute)." in result.output
    assert "No model for orchestrator (tier low)" in result.output
    assert "Run phil setup to choose your models." not in result.output
    assert calls == []


def test_run_with_no_models_prints_the_hint_and_never_runs_setup(git_repo, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("phil.setup.flow.run_setup", lambda *a, **k: called.append(1))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(calc_plan().model_dump_json())
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "run", str(plan_path)])
    assert result.exit_code == 1
    assert "No model for implementer (tier low)" in result.output
    assert "Run phil setup to choose your models." in result.output
    assert called == []


def test_bare_phil_without_a_terminal_prints_the_hint_and_does_not_prompt(git_repo, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    called = []
    monkeypatch.setattr("phil.setup.flow.run_setup", lambda *a, **k: called.append(1))
    result = runner.invoke(cli.app, ["--repo", str(git_repo)])
    assert result.exit_code == 1
    assert "No model for orchestrator (tier low)" in result.output
    assert "Run phil setup to choose your models." in result.output
    assert called == []
