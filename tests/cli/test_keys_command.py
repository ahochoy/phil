from typer.testing import CliRunner

from phil.cli import main as cli
from phil.key_store import get_key, set_key
from phil.ui.theme import make_console

runner = CliRunner()


def wide(monkeypatch):
    # Wide enough that no line wraps, so the assertions can match whole lines.
    monkeypatch.setattr(cli, "console", make_console(width=300))


def stub_secret(monkeypatch, value: str) -> None:
    monkeypatch.setattr(cli, "_read_secret", lambda prompt: value)


def test_keys_set_stores_the_value_and_prints_the_saved_line(git_repo, monkeypatch):
    wide(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    stub_secret(monkeypatch, "sk-TESTSECRET-123")
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "set", "openai"])
    assert result.exit_code == 0, result.output
    assert result.output == "Saved OPENAI_API_KEY for openai in the keychain.\n"
    assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-123"


def test_keys_set_prints_the_env_suffix_when_the_variable_is_also_exported(git_repo, monkeypatch):
    wide(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-TESTSECRET-env")
    stub_secret(monkeypatch, "sk-TESTSECRET-123")
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "set", "openai"])
    assert result.exit_code == 0, result.output
    assert result.output == (
        "Saved OPENAI_API_KEY for openai in the keychain. "
        "(the environment value still wins while OPENAI_API_KEY is exported)\n"
    )


def test_keys_set_aborts_without_saving_on_an_empty_value(git_repo, monkeypatch):
    wide(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    stub_secret(monkeypatch, "")
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "set", "openai"])
    assert result.exit_code == 0, result.output
    assert get_key("OPENAI_API_KEY") is None


def test_keys_set_ollama_is_refused(git_repo, monkeypatch):
    wide(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "set", "ollama"])
    assert result.exit_code == 1
    assert result.output == "ollama doesn't use a key.\n"


def test_keys_set_unknown_provider_prints_the_unknown_provider_message(git_repo, monkeypatch):
    wide(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "set", "nope"])
    assert result.exit_code == 1
    assert result.output == 'Unknown provider "nope". Add [providers.nope] to ~/.phil/config.toml or phil.toml.\n'


def test_keys_list_shows_each_provider_source_in_order_and_never_a_value(git_repo, monkeypatch):
    wide(monkeypatch)
    (git_repo / "phil.toml").write_text(
        '[models]\nhigh = "openai:gpt-4"\nlow = "ollama:small"\n'
        '[providers.custom]\nkind = "openai"\napi_key_env = "CUSTOM_API_KEY"\n'
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-123")  # also planted in the keychain
    monkeypatch.setenv("CUSTOM_API_KEY", "sk-TESTSECRET-123")  # and in the environment
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-TESTSECRET-123")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "list"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "ollama  (no key needed)",
        "openai  OPENAI_API_KEY  keychain",
        "custom  CUSTOM_API_KEY  env",
        "anthropic  ANTHROPIC_API_KEY  env",
    ]
    assert "sk-TESTSECRET-123" not in result.output


def test_keys_remove_prints_both_messages(git_repo, monkeypatch):
    wide(monkeypatch)
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "remove", "openai"])
    assert result.exit_code == 0, result.output
    assert result.output == "No OPENAI_API_KEY in the keychain.\n"

    set_key("OPENAI_API_KEY", "sk-TESTSECRET-123")
    result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "remove", "openai"])
    assert result.exit_code == 0, result.output
    assert result.output == "Removed OPENAI_API_KEY from the keychain.\n"
