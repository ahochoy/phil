from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from phil.agents.check import CHECK_WORD, ModelCheck
from phil.agents.fake import ScriptedAgentFactory
from phil.cli import main as cli
from phil.ui.theme import make_console

runner = CliRunner()

OK = ModelCheck(ok=True, echo=CHECK_WORD)


class TextOnlyFactory:
    def __call__(self, spec, model, workdir, tools, *, timeout_s=180, provider=None):
        return self

    def invoke(self, payload, config=None):
        return {"messages": [AIMessage(content="no tools here")]}


def use(monkeypatch, factory):
    monkeypatch.setattr(cli, "console", make_console(width=300))
    monkeypatch.setattr(cli, "_factory_from_env", lambda: factory)


def test_models_check_prints_one_line_per_model(git_repo, monkeypatch):
    use(monkeypatch, ScriptedAgentFactory({"model_check": [OK, OK]}))
    (git_repo / "phil.toml").write_text('[models]\nhigh = "ollama:big"\nlow = "ollama:small"\n')
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "models", "check"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("✓ high  ollama:big  ") and lines[0].endswith("s")
    assert lines[1].startswith("✓ low  ollama:small  ")


def test_models_check_exits_1_on_a_failure(git_repo, monkeypatch):
    use(monkeypatch, TextOnlyFactory())
    (git_repo / "phil.toml").write_text('[models]\nlow = "ollama:chatty"\n')
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "models", "check"])
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        '✗ low  ollama:chatty  returned text instead of the required structured output: "no tools here"'
    ]


def test_models_check_uses_the_root_set_overrides(git_repo, monkeypatch):
    use(monkeypatch, ScriptedAgentFactory({"model_check": [OK]}))
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "--set", "models.low=ollama:m", "models", "check"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("✓ low  ollama:m  ")


def test_models_check_hints_when_no_models_are_configured(git_repo, monkeypatch):
    use(monkeypatch, ScriptedAgentFactory({}))
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "models", "check"])
    assert result.exit_code == 1
    assert "No models configured" in result.output
    assert "models.high" in result.output
