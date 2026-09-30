from typer.testing import CliRunner

from phil.cli import main as cli
from phil.config import global_config_path
from phil.ui.theme import make_console

runner = CliRunner()


def wide(monkeypatch):
    # Wide enough that no line wraps, so the assertions can match whole lines.
    monkeypatch.setattr(cli, "console", make_console(width=300))


def test_config_prints_the_effective_settings_with_sources(git_repo, monkeypatch):
    wide(monkeypatch)
    (git_repo / "phil.toml").write_text("[run]\nmax_cost_usd = 3.0\n")
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "config"])
    assert result.exit_code == 0, result.output
    assert "max_cost_usd = 3.0  # from phil.toml" in result.output
    assert "model_timeout_s = 180  # from default" in result.output


def test_config_applies_the_root_set_overrides(git_repo, monkeypatch):
    wide(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "--set", "run.max_cost_usd=5", "config"])
    assert result.exit_code == 0, result.output
    assert "max_cost_usd = 5.0  # from --set" in result.output


def test_config_reports_an_invalid_setting(git_repo, monkeypatch):
    wide(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "--set", "run.nope=1", "config"])
    assert result.exit_code == 1
    assert "Invalid --set" in result.output
    assert "Traceback" not in result.output


def test_config_path_shows_both_files_and_whether_they_exist(git_repo, monkeypatch):
    wide(monkeypatch)
    (git_repo / "phil.toml").write_text("")
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "config", "--path"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"global: {global_config_path()} (missing)",
        f"repo: {git_repo / 'phil.toml'} (exists)",
    ]
    global_config_path().parent.mkdir(parents=True, exist_ok=True)
    global_config_path().write_text("")
    (git_repo / "phil.toml").unlink()
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "config", "--path"])
    assert result.output.splitlines() == [
        f"global: {global_config_path()} (exists)",
        f"repo: {git_repo / 'phil.toml'} (missing)",
    ]
