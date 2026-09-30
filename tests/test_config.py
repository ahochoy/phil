import tomllib
from pathlib import Path

import pytest

from phil.config import ConfigError, PhilConfig, effective_toml, global_config_path, load_config, parse_override
from phil.store.paths import phil_home


def test_missing_file_gives_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.run.tester_mode == "run"
    assert config.run.max_attempts_per_phase == 3
    assert config.run.model_timeout_s == 180
    assert config.run.warn_at == 0.8
    # every model call is counted since 4c (sub-agents, failed tries), so the token ceiling is
    # generous; cost stays the primary guard
    assert config.run.max_tokens == 1_500_000
    assert config.run.max_cost_usd == 2.0
    assert config.models == {}


def test_model_timeout_s_and_warn_at_are_loaded(tmp_path):
    (tmp_path / "phil.toml").write_text("[run]\nmodel_timeout_s = 60\nwarn_at = 0.5\n")
    config = load_config(tmp_path)
    assert config.run.model_timeout_s == 60
    assert config.run.warn_at == 0.5


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_model_timeout_s_is_rejected(tmp_path, value):
    (tmp_path / "phil.toml").write_text(f"[run]\nmodel_timeout_s = {value}\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


@pytest.mark.parametrize("value", [0, 1, -0.1, 1.1])
def test_warn_at_outside_open_unit_interval_is_rejected(tmp_path, value):
    (tmp_path / "phil.toml").write_text(f"[run]\nwarn_at = {value}\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_models_must_be_set_per_role(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nimplementer = "openrouter:cheap/model"\n')
    config = load_config(tmp_path)
    assert config.model_for("implementer") == "openrouter:cheap/model"
    with pytest.raises(ConfigError, match=r'reviewer = "provider:model"'):
        config.model_for("reviewer")


def test_missing_models_lists_unset_roles(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nimplementer = "openrouter:cheap/model"\n')
    config = load_config(tmp_path)
    assert config.missing_models(("implementer", "tester", "reviewer")) == ["tester", "reviewer"]


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
    with pytest.raises(ConfigError, match="wizard"):
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


def test_missing_keys_names_the_env_var_for_each_provider(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[models]\nimplementer = "openrouter:openai/gpt-6-sol"\ntester = "openrouter:openai/gpt-6-luna"\n'
        'reviewer = "anthropic:claude-sonnet-5"\ncritic = "local:llama"\n'
    )
    config = load_config(tmp_path)
    roles = ("implementer", "tester", "reviewer", "critic", "architect")
    assert config.missing_keys(roles, environ={}) == ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"]
    assert config.missing_keys(roles, environ={"OPENROUTER_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}) == []
    assert config.missing_keys(roles, environ={"OPENROUTER_API_KEY": ""}) == ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"]


def _write_global(text: str) -> Path:
    path = phil_home() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_global_config_path_is_under_phil_home():
    assert global_config_path() == phil_home() / "config.toml"


def test_global_values_apply_when_the_repo_has_no_phil_toml(tmp_path):
    path = _write_global("[run]\nmax_cost_usd = 7.0\n")
    config = load_config(tmp_path)
    assert config.run.max_cost_usd == 7.0
    assert config.sources["run.max_cost_usd"] == str(path)
    assert config.sources["run.max_tokens"] == "default"


def test_repo_overrides_global_key_by_key(tmp_path):
    path = _write_global("[run]\nmax_cost_usd = 7.0\nwarn_at = 0.5\n")
    (tmp_path / "phil.toml").write_text("[run]\nmax_cost_usd = 3.0\n")
    config = load_config(tmp_path)
    assert config.run.max_cost_usd == 3.0
    assert config.sources["run.max_cost_usd"] == "phil.toml"
    assert config.run.warn_at == 0.5
    assert config.sources["run.warn_at"] == str(path)


def test_lists_replace(tmp_path):
    _write_global('[shell]\nallow = ["a"]\n')
    (tmp_path / "phil.toml").write_text('[shell]\nallow = ["b"]\n')
    config = load_config(tmp_path)
    assert config.shell.allow == ["b"]
    assert config.sources["shell.allow"] == "phil.toml"


def test_set_wins_over_every_file(tmp_path):
    _write_global("[run]\nmax_cost_usd = 7.0\n")
    (tmp_path / "phil.toml").write_text("[run]\nmax_cost_usd = 3.0\n")
    config = load_config(tmp_path, overrides=["run.max_cost_usd=5"])
    assert config.run.max_cost_usd == 5.0
    assert config.sources["run.max_cost_usd"] == "--set"


def test_set_parses_toml_values_and_bare_strings(tmp_path):
    assert parse_override("models.high=openrouter:x/y") == (["models", "high"], "openrouter:x/y")
    assert parse_override("run.max_tokens=100") == (["run", "max_tokens"], 100)
    assert parse_override('shell.allow=["ls"]') == (["shell", "allow"], ["ls"])
    config = load_config(tmp_path, overrides=["run.max_tokens=100", 'shell.allow=["ls"]'])
    assert config.run.max_tokens == 100
    assert config.shell.allow == ["ls"]


@pytest.mark.parametrize("text", ["run.max_cost_usd", "=5"])
def test_set_without_equals_is_a_config_error(tmp_path, text):
    with pytest.raises(ConfigError, match="--set expects key.path=value"):
        parse_override(text)
    with pytest.raises(ConfigError, match="--set expects key.path=value"):
        load_config(tmp_path, overrides=[text])


def test_set_unknown_path_names_set_in_the_error(tmp_path):
    with pytest.raises(ConfigError, match=r"Invalid --set: run\.nope"):
        load_config(tmp_path, overrides=["run.nope=1"])


def test_invalid_global_file_names_the_file(tmp_path):
    path = _write_global('[run]\nmax_cost_usd = "x"\n')
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path)
    assert f"Invalid {path}: run.max_cost_usd: " in str(info.value)


def test_malformed_global_file_names_the_file(tmp_path):
    path = _write_global("[run\n")
    with pytest.raises(ConfigError, match="Invalid " + str(path).replace("\\", "\\\\")):
        load_config(tmp_path)


def test_unknown_role_in_the_global_file_names_the_file(tmp_path):
    path = _write_global('[models]\nwizard = "openrouter:x/y"\n')
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path)
    assert f"Invalid {path}: models: " in str(info.value)
    assert "wizard" in str(info.value)


def test_invalid_repo_value_names_phil_toml(tmp_path):
    _write_global("[run]\nmax_cost_usd = 7.0\n")
    (tmp_path / "phil.toml").write_text('[run]\nmax_cost_usd = "x"\n')
    with pytest.raises(ConfigError, match=r"Invalid phil\.toml: run\.max_cost_usd: "):
        load_config(tmp_path)


def test_effective_toml_marks_sources(tmp_path):
    path = _write_global("[run]\nwarn_at = 0.5\n")
    (tmp_path / "phil.toml").write_text('[run]\nmax_cost_usd = 3.0\n[models]\ncritic = "openrouter:x/y"\n')
    config = load_config(tmp_path, overrides=["run.max_tokens=100"])
    text = effective_toml(config)
    assert "max_cost_usd = 3.0  # from phil.toml" in text
    assert f"warn_at = 0.5  # from {path}" in text
    assert "max_tokens = 100  # from --set" in text
    assert "model_timeout_s = 180  # from default" in text
    assert 'critic = "openrouter:x/y"  # from phil.toml' in text
    # it is valid TOML that loads back to the same settings
    reloaded = PhilConfig(**tomllib.loads(text))
    assert reloaded.model_dump() == config.model_dump()
