import pytest
from pydantic import ValidationError

from phil.config import DEFAULT_MODEL, ROLES, load_config


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
    assert config.budget_for("reviewer").max_input_tokens == 12000
    assert config.project.test_cmd == "uv run pytest -q"
    assert config.shell.allow == ["pytest*"]


def test_invalid_tester_mode_is_rejected(tmp_path):
    (tmp_path / "phil.toml").write_text('[run]\ntester_mode = "sometimes"\n')
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_unknown_role_raises(tmp_path):
    with pytest.raises(KeyError):
        load_config(tmp_path).model_for("wizard")
