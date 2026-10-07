import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from phil.config import (
    CHAT_ROLES,
    DEFAULT_TIERS,
    ROLES,
    ConfigError,
    PhilConfig,
    effective_toml,
    global_config_path,
    load_config,
    parse_override,
)
from phil.store.paths import phil_home
from tests.helpers import TEST_MODELS


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
    assert config.run.quick_max_attempts == 2
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


def test_quick_max_attempts_below_one_is_rejected():
    with pytest.raises(ValidationError):
        PhilConfig.model_validate({"run": {"quick_max_attempts": 0}})


def test_models_must_be_set_per_role(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nimplementer = "openrouter:cheap/model"\n')
    config = load_config(tmp_path)
    assert config.model_for("implementer") == "openrouter:cheap/model"
    with pytest.raises(ConfigError, match=r"No model for reviewer \(tier high\)"):
        config.model_for("reviewer")


def test_missing_models_lists_unset_roles(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nimplementer = "openrouter:cheap/model"\n')
    config = load_config(tmp_path)
    assert config.missing_models(("implementer", "tester", "reviewer")) == ["tester", "reviewer"]


def test_high_and_low_tiers_resolve_the_default_roles(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nhigh = "openrouter:big/model"\nlow = "openrouter:small/model"\n')
    config = load_config(tmp_path)
    assert config.model_for("architect") == "openrouter:big/model"
    assert config.model_for("implementer") == "openrouter:small/model"


def test_a_role_key_beats_its_tier(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[models]\nhigh = "openrouter:big/model"\narchitect = "openrouter:special/model"\n'
    )
    config = load_config(tmp_path)
    assert config.model_for("architect") == "openrouter:special/model"


def test_tiers_table_remaps_a_role_to_another_tier(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[models]\nhigh = "openrouter:big/model"\nlow = "openrouter:small/model"\n'
        '[tiers]\nimplementer = "high"\n'
    )
    config = load_config(tmp_path)
    assert config.tier_for("implementer") == "high"
    assert config.model_for("implementer") == "openrouter:big/model"


def test_classifier_falls_back_to_low(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nlow = "openrouter:small/model"\n')
    config = load_config(tmp_path)
    assert config.tier_model("classifier") == "openrouter:small/model"


def test_legacy_six_role_config_resolves_each_role_to_its_own_key(tmp_path):
    lines = "".join(f'{role} = "openrouter:{role}/model"\n' for role in TEST_MODELS)
    (tmp_path / "phil.toml").write_text("[models]\n" + lines)
    config = load_config(tmp_path)
    for role in TEST_MODELS:
        assert config.model_for(role) == f"openrouter:{role}/model"


def test_with_only_high_set_implementer_is_missing_with_low_tier_in_the_message(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nhigh = "openrouter:big/model"\n')
    config = load_config(tmp_path)
    assert config.missing_models(("implementer",)) == ["implementer"]
    with pytest.raises(ConfigError, match=r"\(tier low\)"):
        config.model_for("implementer")


def test_unknown_tier_in_tiers_is_rejected(tmp_path):
    (tmp_path / "phil.toml").write_text('[tiers]\nimplementer = "medium"\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_tier_resolution_spans_the_global_and_repo_layers(tmp_path):
    _write_global('[models]\nhigh = "openrouter:big/model"\nlow = "openrouter:small/model"\n')
    (tmp_path / "phil.toml").write_text('[models]\narchitect = "openrouter:special/model"\n')
    config = load_config(tmp_path)
    # The repo's own role key wins over the global file's tier.
    assert config.model_for("architect") == "openrouter:special/model"
    # Roles with no role key of their own still resolve through the global file's tier models.
    assert config.model_for("critic") == "openrouter:big/model"
    assert config.model_for("implementer") == "openrouter:small/model"


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


def test_setup_cmd_defaults_to_none_and_setup_timeout_s_to_600(tmp_path):
    config = load_config(tmp_path)
    assert config.project.setup_cmd is None
    assert config.project.setup_timeout_s == 600


def test_setup_cmd_and_timeout_are_loaded(tmp_path):
    (tmp_path / "phil.toml").write_text('[project]\nsetup_cmd = "npm ci"\nsetup_timeout_s = 120\n')
    config = load_config(tmp_path)
    assert config.project.setup_cmd == "npm ci"
    assert config.project.setup_timeout_s == 120


def test_setup_cmd_can_be_set_to_empty_string_for_no_setup(tmp_path):
    (tmp_path / "phil.toml").write_text('[project]\nsetup_cmd = ""\n')
    config = load_config(tmp_path)
    assert config.project.setup_cmd == ""


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_setup_timeout_s_is_rejected(tmp_path, value):
    (tmp_path / "phil.toml").write_text(f"[project]\nsetup_timeout_s = {value}\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


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


def test_missing_keys_groups_roles_by_provider(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[models]\nimplementer = "openrouter:openai/gpt-6-sol"\ntester = "openrouter:openai/gpt-6-luna"\n'
        'reviewer = "anthropic:claude-sonnet-5"\n'
    )
    config = load_config(tmp_path)
    roles = ("implementer", "tester", "reviewer", "architect")
    expected = [
        "openrouter needs OPENROUTER_API_KEY (used by implementer, tester).",
        "anthropic needs ANTHROPIC_API_KEY (used by reviewer).",
    ]
    assert config.missing_keys(roles, environ={}) == expected
    assert config.missing_keys(roles, environ={"OPENROUTER_API_KEY": ""}) == expected
    assert config.missing_keys(roles, environ={"OPENROUTER_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}) == []


def test_missing_keys_names_a_tier_model_by_its_provider_alias(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nhigh = "google_genai:gemini-2.5-pro"\n')
    config = load_config(tmp_path)
    assert config.missing_keys(("architect", "critic"), environ={}) == [
        "google needs GOOGLE_API_KEY (used by architect, critic)."
    ]


def test_ollama_needs_no_key(tmp_path):
    (tmp_path / "phil.toml").write_text('[models]\nlow = "ollama:qwen3:32b"\n')
    assert load_config(tmp_path).missing_keys(("implementer", "tester"), environ={}) == []


def test_a_custom_provider_key_is_checked(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[models]\nlow = "lab:llama"\n[providers.lab]\nkind = "openai"\napi_key_env = "LAB_KEY"\n'
    )
    config = load_config(tmp_path)
    assert config.missing_keys(("implementer",), environ={}) == ["lab needs LAB_KEY (used by implementer)."]
    assert config.missing_keys(("implementer",), environ={"LAB_KEY": "k"}) == []


def test_missing_keys_finds_a_store_only_key(tmp_path, monkeypatch):
    from phil.key_store import set_key

    (tmp_path / "phil.toml").write_text('[models]\nimplementer = "openai:gpt-5-mini"\n')
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = load_config(tmp_path)
    assert config.missing_keys(("implementer",)) == ["openai needs OPENAI_API_KEY (used by implementer)."]
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    assert config.missing_keys(("implementer",)) == []


def test_missing_keys_reports_an_unknown_provider(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[models]\nhigh = "local:llama"\nimplementer = "nowhere:x"\ntester = "openrouter:openai/gpt-6-luna"\n'
    )
    config = load_config(tmp_path)
    assert config.missing_keys(("architect", "critic", "implementer", "tester"), environ={}) == [
        'Unknown provider "local" in high model "local:llama". Add [providers.local] to ~/.phil/config.toml or phil.toml.',
        'Unknown provider "nowhere" in implementer model "nowhere:x". '
        "Add [providers.nowhere] to ~/.phil/config.toml or phil.toml.",
        "openrouter needs OPENROUTER_API_KEY (used by tester).",
    ]


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


def test_effective_toml_prints_no_empty_table_headers(tmp_path):
    text = effective_toml(load_config(tmp_path))
    for header in ("[models]", "[tiers]", "[budget]", "[providers]"):
        assert header not in text
    (tmp_path / "phil.toml").write_text('[providers.lab]\nkind = "openai"\n')
    text = effective_toml(load_config(tmp_path))
    assert "[providers]\n" not in text
    assert '[providers.lab]\nkind = "openai"  # from phil.toml\n' in text


def test_classifier_and_answerer_roles_resolve_through_their_tiers():
    config = PhilConfig(models={"high": "openrouter:h", "low": "openrouter:l"})
    assert "classifier" in ROLES and "answerer" in ROLES
    assert DEFAULT_TIERS["classifier"] == "classifier"
    assert config.model_for("classifier") == "openrouter:l"  # classifier tier falls back to low
    assert config.model_owner("classifier") == "low"
    assert config.model_for("answerer") == "openrouter:l"
    assert "answerer" not in CHAT_ROLES and "classifier" not in CHAT_ROLES


def test_routing_defaults_and_bounds():
    config = PhilConfig()
    assert config.routing.confidence_threshold == 0.5
    assert config.routing.detail_threshold == 0.6
    assert config.routing.jev_timeout_s == 5.0
    with pytest.raises(ValueError):
        PhilConfig.model_validate({"routing": {"confidence_threshold": 1.5}})
    with pytest.raises(ValueError):
        PhilConfig.model_validate({"routing": {"jev_timeout_s": 0}})


def test_typesafe_classifier_is_allowed():
    config = PhilConfig(models={"low": "openrouter:l", "classifier": "typesafe:jev-latest"})
    assert config.model_for("classifier") == "typesafe:jev-latest"
    assert config.is_systemone("classifier") is True
    assert config.is_systemone("orchestrator") is False


@pytest.mark.parametrize(
    "models",
    [
        {"low": "typesafe:jev-latest"},  # every low role, not just the classifier
        {"low": "openrouter:l", "orchestrator": "typesafe:jev-latest"},
    ],
)
def test_typesafe_on_any_other_role_is_a_config_error(models):
    with pytest.raises(ValueError, match="typesafe.*only.*classifier"):
        PhilConfig(models=models)


def test_the_systemone_role_error_names_the_actual_provider():
    with pytest.raises(ValueError, match="the openrouter_decisions provider \\(kind systemone\\)"):
        PhilConfig(models={"low": "openrouter_decisions:typesafe/jev-1.13"})


def test_jev_gets_its_own_detail_threshold_when_set():
    routing = PhilConfig.model_validate({"routing": {"jev_detail_threshold": 0.5}}).routing
    assert routing.detail_threshold_for("jev") == 0.5
    assert routing.detail_threshold_for("llm") == 0.6
    assert routing.detail_threshold_for(None) == 0.6  # router unavailable: the default


def test_without_a_jev_threshold_every_source_uses_the_default():
    routing = PhilConfig().routing
    assert routing.jev_detail_threshold is None
    assert routing.detail_threshold_for("jev") == routing.detail_threshold == 0.6


def test_the_jev_detail_threshold_is_a_probability():
    with pytest.raises(ValueError):
        PhilConfig.model_validate({"routing": {"jev_detail_threshold": 1.2}})
