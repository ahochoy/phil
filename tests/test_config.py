from pathlib import Path

import pytest

from phil.config import DEFAULT_MODEL, ROLES, ConfigError, load_config


def test_missing_file_gives_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.run.tester_mode == "run"
    assert config.run.max_attempts_per_phase == 3
    assert all(config.model_for(role) == DEFAULT_MODEL for role in ROLES)


def test_partial_models_override_keeps_other_defaults(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nimplementer = "openrouter:cheap/model"\n')
    config = load_config(tmp_path)
    assert config.model_for("implementer") == "openrouter:cheap/model"
    assert config.model_for("reviewer") == DEFAULT_MODEL


def test_sections_are_loaded(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[run]\ntester_mode = "task+run"\n'
        "[budget.implementer]\nmax_input_tokens = 8000\n"
        '[project]\ntest_cmd = "uv run pytest -q"\n'
        '[shell]\nallow = ["pytest*"]\n'
    )
    config = load_config(tmp_path)
    assert config.run.tester_mode == "task+run"
    assert config.budget_for("implementer").max_input_tokens == 8000
    assert config.budget_for("reviewer").max_input_tokens == 48000
    assert config.project.test_cmd == "uv run pytest -q"
    assert config.shell.allow == ["pytest*"]


def test_invalid_tester_mode_is_rejected(tmp_path):
    (tmp_path / "phil.toml").write_text('[run]\ntester_mode = "sometimes"\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_unknown_role_raises(tmp_path):
    with pytest.raises(KeyError):
        load_config(tmp_path).model_for("wizard")


def test_typo_model_role_raises_config_error(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nimplmenter = "openrouter:cheap/model"\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_typo_budget_role_raises_config_error(tmp_path):
    (tmp_path / "phil.toml").write_text("[budget.implmenter]\nmax_input_tokens = 8000\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_malformed_toml_raises_config_error(tmp_path):
    (tmp_path / "phil.toml").write_text("[run\ntester_mode = run\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_pass_env_defaults_empty_and_loads(tmp_path):
    assert load_config(tmp_path).shell.pass_env == []
    (tmp_path / "phil.toml").write_text('[shell]\npass_env = ["DATABASE_TOKEN"]\n')
    assert load_config(tmp_path).shell.pass_env == ["DATABASE_TOKEN"]


def test_role_budget_defaults():
    config = load_config(Path("/nonexistent"))
    assert config.budget_for("implementer").max_input_tokens == 12000
    assert config.budget_for("architect").max_input_tokens == 24000
    assert config.budget_for("tester").max_input_tokens == 48000


def test_git_defaults():
    config = load_config(Path("/nonexistent"))
    assert config.git.sign_commits == "auto"
    assert config.git.run_hooks is False


def test_git_section_is_loaded(tmp_path):
    (tmp_path / "phil.toml").write_text("[git]\nsign_commits = true\nrun_hooks = true\n")
    config = load_config(tmp_path)
    assert config.git.sign_commits is True
    assert config.git.run_hooks is True


def test_invalid_sign_commits_is_rejected(tmp_path):
    (tmp_path / "phil.toml").write_text('[git]\nsign_commits = "sometimes"\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)
