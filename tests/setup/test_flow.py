import pytest

from phil.agents.check import CheckResult
from phil.config import global_config_path, load_config
from phil.key_store import SERVICE
from phil.setup.catalog import CatalogModel
from phil.setup.flow import run_setup
from phil.setup.io import ScriptedSetupIO

SECRET = "sk-TESTSECRET-123"
SUGGESTED_HIGH = "openrouter:anthropic/claude-sonnet-5"
SUGGESTED_LOW = "openrouter:google/gemini-3.8-flash"
CATALOG = [
    CatalogModel("anthropic/claude-sonnet-5", 3.0, 15.0),
    CatalogModel("google/gemini-3.8-flash", 0.3, 2.5),
    CatalogModel("openai/gpt-6-sol", 2.0, 10.0),
    CatalogModel("openai/gpt-6-luna", 0.1, 0.5),
]


class FakeCheck:
    """Results for the candidate's tiers, the same shape `check_models` gives: one per distinct
    model, labelled with the tiers that use it. Models in `bad` fail."""

    def __init__(self, bad=(), detail="no", interrupt=False):
        self.bad = set(bad)
        self.detail = detail
        self.interrupt = interrupt
        self.configs = []

    def __call__(self, config):
        self.configs.append(config)
        if self.interrupt:
            raise KeyboardInterrupt
        labels: dict[str, list[str]] = {}
        for tier in ("high", "low"):
            labels.setdefault(config.models[tier], []).append(tier)
        return [
            CheckResult(", ".join(tiers), model, model not in self.bad, 0.1, self.detail if model in self.bad else "")
            for model, tiers in labels.items()
        ]


class FakeOllama:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def __call__(self, base_url):
        self.urls.append(base_url)
        return self.answers.pop(0)


@pytest.fixture(autouse=True)
def no_provider_keys(monkeypatch):
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "LAB_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def setup(answers, tmp_path, *, check=None, ollama=None, config=None, catalog=None, **kwargs):
    io = ScriptedSetupIO(answers)
    check = check or FakeCheck()
    wrote = run_setup(
        io,
        config=config or load_config(tmp_path),
        check=check,
        catalog=catalog or (lambda: CATALOG),
        ollama=ollama or FakeOllama(),
        **kwargs,
    )
    return wrote, io, check


@pytest.fixture
def no_keychain(monkeypatch):
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: FailKeyring())


def written(tmp_path):
    return load_config(tmp_path)


def test_a_fresh_openrouter_setup_stores_the_key_writes_the_tiers_and_checks_once(tmp_path, memory_keyring):
    wrote, io, check = setup(["", SECRET, "", ""], tmp_path)
    assert wrote is True
    assert memory_keyring.store == {(SERVICE, "OPENROUTER_API_KEY"): SECRET}
    assert "Saved OPENROUTER_API_KEY for openrouter in the keychain." in io.lines
    config = written(tmp_path)
    assert config.models == {"high": SUGGESTED_HIGH, "low": SUGGESTED_LOW}
    assert len(check.configs) == 1
    text = global_config_path().read_text()
    assert SECRET not in text
    assert all(SECRET not in line for line in io.lines)
    assert str(global_config_path()) in "\n".join(io.lines)
    assert any("phil keys list" in line for line in io.lines)


def test_an_existing_env_key_is_not_asked_for(tmp_path, monkeypatch, memory_keyring):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    wrote, io, _ = setup(["", "", ""], tmp_path)
    assert wrote is True
    assert "Using OPENROUTER_API_KEY from env." in io.lines
    assert not [kind for kind, _ in io.prompts if kind == "secret"]
    assert memory_keyring.store == {}


def test_a_keychain_key_can_be_kept_or_replaced(tmp_path, memory_keyring):
    memory_keyring.store[(SERVICE, "OPENROUTER_API_KEY")] = "sk-TESTOLD-1"
    wrote, io, _ = setup(["", "Keep", "", ""], tmp_path)
    assert wrote is True
    assert "Using OPENROUTER_API_KEY from keychain." in io.lines
    assert memory_keyring.store[(SERVICE, "OPENROUTER_API_KEY")] == "sk-TESTOLD-1"

    wrote, _, _ = setup(["", "Replace", SECRET, "", ""], tmp_path)
    assert wrote is True
    assert memory_keyring.store[(SERVICE, "OPENROUTER_API_KEY")] == SECRET


def test_no_keychain_says_so_and_carries_on(tmp_path, no_keychain):
    wrote, io, _ = setup(["", "", ""], tmp_path)  # no key is asked for: there is nowhere to keep it
    assert wrote is True
    assert "No keychain is available here; export OPENROUTER_API_KEY instead." in io.lines
    assert not [kind for kind, _ in io.prompts if kind == "secret"]


MISSING_KEY = "openrouter needs OPENROUTER_API_KEY (used by high, low)."


def test_a_missing_key_in_the_check_repeats_the_hint_instead_of_offering_another_model(tmp_path, no_keychain):
    check = FakeCheck(bad={SUGGESTED_HIGH, SUGGESTED_LOW}, detail=MISSING_KEY)
    wrote, io, _ = setup(["", "", "", "Keep"], tmp_path, check=check)
    assert wrote is True
    assert len(check.configs) == 1
    assert not any("Choose a different" in line for line in io.lines)
    assert "OPENROUTER_API_KEY isn't set: export OPENROUTER_API_KEY, or run `phil keys set openrouter`." in io.lines
    assert written(tmp_path).models == {"high": SUGGESTED_HIGH, "low": SUGGESTED_LOW}


def test_a_missing_key_in_the_check_can_cancel(tmp_path, no_keychain):
    check = FakeCheck(bad={SUGGESTED_HIGH}, detail=MISSING_KEY)
    wrote, io, _ = setup(["", "", "", "Cancel"], tmp_path, check=check)
    assert wrote is False
    assert not global_config_path().exists()
    assert "Setup cancelled; nothing was written." in io.lines


def test_a_key_left_empty_then_missing_in_the_check(tmp_path):
    check = FakeCheck(bad={SUGGESTED_HIGH}, detail=MISSING_KEY)
    wrote, io, _ = setup(["", "", "", "", "Keep"], tmp_path, check=check)
    assert wrote is True
    assert not any("Choose a different" in line for line in io.lines)


def test_a_key_store_error_is_said_and_setup_carries_on(tmp_path, monkeypatch):
    from phil.key_store import KeyStoreError

    def failing(var, value):
        raise KeyStoreError(f"Couldn't save {var} to the keychain: OSError")

    monkeypatch.setattr("phil.setup.flow.set_key", failing)
    wrote, io, _ = setup(["", SECRET, "", ""], tmp_path)
    assert wrote is True
    assert "Couldn't save OPENROUTER_API_KEY to the keychain: OSError" in io.lines


def test_openrouter_typed_text_searches_the_catalog(tmp_path):
    wrote, io, _ = setup(["", SECRET, "gpt-6", "Type again", "luna", "1", "openrouter:openai/gpt-6-sol"], tmp_path)
    assert wrote is True
    assert written(tmp_path).models == {"high": "openrouter:openai/gpt-6-luna", "low": "openrouter:openai/gpt-6-sol"}
    assert any("openai/gpt-6-sol  $2.00/$10.00 per M" in line for line in io.lines)
    assert io.lines.count("Fetching the OpenRouter model list…") == 1


def test_the_catalog_is_not_fetched_when_the_defaults_are_accepted(tmp_path):
    wrote, io, _ = setup(["", SECRET, "", ""], tmp_path)
    assert wrote is True
    assert "Fetching the OpenRouter model list…" not in io.lines


@pytest.mark.parametrize("typed", ["vendor/brand-new", "openrouter:vendor/brand-new"])
def test_an_openrouter_id_with_no_match_can_be_used_as_typed(tmp_path, typed):
    wrote, io, _ = setup(["", SECRET, typed, "Use", ""], tmp_path)
    assert wrote is True
    assert "  1. Use 'vendor/brand-new' as typed" in io.lines
    assert written(tmp_path).models["high"] == "openrouter:vendor/brand-new"


def test_an_openrouter_search_also_offers_the_id_as_typed(tmp_path):
    wrote, _, _ = setup(["", SECRET, "gpt-6-s", "Use", ""], tmp_path)
    assert wrote is True
    assert written(tmp_path).models["high"] == "openrouter:gpt-6-s"


def test_a_tier_no_role_maps_to_is_shown_as_unused(tmp_path, git_repo):
    # Every high-tier role remapped to "low", so no role maps through "high" and it's reported as
    # unused. ("low" can't be used for this: the classifier role falls back to it whenever it has
    # no model of its own, so "low" is always in use once it's set.)
    (git_repo / "phil.toml").write_text('[tiers]\narchitect = "low"\ncritic = "low"\nreviewer = "low"\n')
    wrote, io, _ = setup(["", SECRET, "", ""], tmp_path, config=load_config(git_repo))
    assert wrote is True
    assert f"– high  {SUGGESTED_HIGH}  unused (no role maps to it)" in io.lines


def test_a_write_error_is_said_and_nothing_is_reported_as_written(tmp_path):
    def failing_write(path, **kwargs):
        raise OSError("Read-only file system")

    wrote, io, _ = setup(["", SECRET, "", ""], tmp_path, write=failing_write)
    assert wrote is False
    assert f"Couldn't write {global_config_path()}: Read-only file system" in io.lines
    assert not any(line.startswith("Wrote ") for line in io.lines)


def test_a_toml_error_in_the_write_is_said(tmp_path):
    from tomlkit.exceptions import ParseError

    def failing_write(path, **kwargs):
        raise ParseError(1, 1, "bad")

    wrote, io, _ = setup(["", SECRET, "", ""], tmp_path, write=failing_write)
    assert wrote is False
    assert any(line.startswith(f"Couldn't write {global_config_path()}: ") for line in io.lines)


def test_ctrl_c_during_the_check_cancels(tmp_path):
    wrote, io, _ = setup(["", SECRET, "", ""], tmp_path, check=FakeCheck(interrupt=True))
    assert wrote is False
    assert not global_config_path().exists()
    assert "Setup cancelled; the key was saved, nothing else was written." in io.lines


def test_ctrl_c_during_the_catalog_fetch_cancels(tmp_path):
    def interrupted():
        raise KeyboardInterrupt

    wrote, io, _ = setup(["", "", "gpt"], tmp_path, catalog=interrupted)
    assert wrote is False
    assert "Setup cancelled; nothing was written." in io.lines


def test_the_ollama_path_picks_installed_models(tmp_path, memory_keyring):
    ollama = FakeOllama(["qwen3:32b", "llama4:8b"])
    wrote, io, _ = setup(["Ollama", "1", "2"], tmp_path, ollama=ollama)
    assert wrote is True
    assert ollama.urls == ["http://localhost:11434/v1"]
    config = written(tmp_path)
    assert config.models == {"high": "ollama:qwen3:32b", "low": "ollama:llama4:8b"}
    assert config.providers == {}
    assert memory_keyring.store == {}
    assert not [kind for kind, _ in io.prompts if kind == "secret"]


def test_unreachable_ollama_then_retry(tmp_path):
    ollama = FakeOllama(None, ["qwen3:32b"])
    wrote, io, _ = setup(["Ollama", "Retry", "", ""], tmp_path, ollama=ollama)
    assert wrote is True
    assert any("ollama serve" in line for line in io.lines)
    assert written(tmp_path).models == {"high": "ollama:qwen3:32b", "low": "ollama:qwen3:32b"}


def test_unreachable_ollama_then_back_to_the_provider_choice(tmp_path):
    ollama = FakeOllama(None)
    wrote, _, _ = setup(["Ollama", "Back", "OpenAI", SECRET, "", "gpt-x"], tmp_path, ollama=ollama)
    assert wrote is True
    assert written(tmp_path).models["low"] == "openai:gpt-x"


def test_the_custom_provider_writes_a_providers_table(tmp_path, memory_keyring):
    answers = ["Custom", "Lab!", "openai", "lab", "http://lab:8000/v1", "", SECRET, "big", "lab:small"]
    wrote, io, check = setup(answers, tmp_path)
    assert wrote is True
    assert any("isn't a valid provider name" in line for line in io.lines)
    assert any("built-in provider" in line for line in io.lines)
    config = written(tmp_path)
    assert config.models == {"high": "lab:big", "low": "lab:small"}
    entry = config.providers["lab"]
    assert (entry.kind, entry.base_url, entry.api_key_env) == ("openai", "http://lab:8000/v1", "LAB_API_KEY")
    assert memory_keyring.store == {(SERVICE, "LAB_API_KEY"): SECRET}
    assert check.configs[0].providers["lab"].base_url == "http://lab:8000/v1"
    assert SECRET not in global_config_path().read_text()


def test_a_keyless_custom_provider(tmp_path):
    wrote, io, _ = setup(["Custom", "local", "http://box:8080/v1", "none", "m1", "m2"], tmp_path)
    assert wrote is True
    assert written(tmp_path).providers["local"].api_key_env is None
    assert not [kind for kind, _ in io.prompts if kind == "secret"]


def test_a_failed_check_then_a_different_choice_then_the_check_passes(tmp_path):
    check = FakeCheck(bad={SUGGESTED_HIGH})
    answers = ["", SECRET, "", "", "Choose a different", "openai/gpt-6-sol"]
    wrote, io, _ = setup(answers, tmp_path, check=check)
    assert wrote is True
    assert len(check.configs) == 2
    assert written(tmp_path).models == {"high": "openrouter:openai/gpt-6-sol", "low": SUGGESTED_LOW}
    assert any(line.startswith("✗ high") for line in io.lines)
    assert any(line.startswith("✓ high") for line in io.lines)


def test_a_failed_check_can_be_kept_anyway(tmp_path):
    check = FakeCheck(bad={SUGGESTED_HIGH})
    wrote, _, _ = setup(["", SECRET, "", "", "Keep"], tmp_path, check=check)
    assert wrote is True
    assert len(check.configs) == 1
    assert written(tmp_path).models["high"] == SUGGESTED_HIGH


NOTHING_WRITTEN = "Setup cancelled; nothing was written."
KEY_SAVED = "Setup cancelled; the key was saved, nothing else was written."


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ([], NOTHING_WRITTEN),  # at the provider choice
        ([""], NOTHING_WRITTEN),  # at the key prompt, before set_key
        (["", SECRET], KEY_SAVED),  # at the high model
        (["", SECRET, ""], KEY_SAVED),  # at the low model
        (["", SECRET, "", ""], KEY_SAVED),  # at the failed-check choice
        (["Custom", "lab"], NOTHING_WRITTEN),  # inside the custom provider questions
        (["Ollama"], NOTHING_WRITTEN),  # at the unreachable-Ollama choice
    ],
)
def test_a_cancel_at_any_step_writes_nothing(tmp_path, memory_keyring, answers, message):
    check = FakeCheck(bad={SUGGESTED_HIGH})
    wrote, io, _ = setup(answers, tmp_path, check=check, ollama=FakeOllama(None))
    assert wrote is False
    assert not global_config_path().exists()
    assert message in io.lines
    if message == NOTHING_WRITTEN:
        assert memory_keyring.store == {}


def test_a_rerun_prefills_the_current_models_and_ignores_role_keys_in_the_check(tmp_path, git_repo, memory_keyring):
    memory_keyring.store[(SERVICE, "OPENROUTER_API_KEY")] = "sk-TESTOLD-1"
    global_config_path().parent.mkdir(parents=True)
    global_config_path().write_text(
        '# mine\n[models]\nhigh = "openrouter:openai/gpt-6-sol"\nlow = "openrouter:openai/gpt-6-luna"\n'
    )
    (git_repo / "phil.toml").write_text('[models]\ncritic = "openrouter:openai/gpt-6-sol"\n')
    io = ScriptedSetupIO(["", "", "", ""])
    check = FakeCheck()
    wrote = run_setup(
        io, config=load_config(git_repo), check=check, catalog=lambda: CATALOG, ollama=FakeOllama()
    )
    assert wrote is True
    assert ("ask", "high model") in io.prompts
    config = load_config(tmp_path)
    assert config.models == {"high": "openrouter:openai/gpt-6-sol", "low": "openrouter:openai/gpt-6-luna"}
    assert "critic" not in check.configs[0].models
    assert global_config_path().read_text().startswith("# mine")
    assert any("critic" in line for line in io.lines)  # the role override is pointed out


def test_repo_and_set_values_are_not_prefilled(tmp_path, git_repo):
    (git_repo / "phil.toml").write_text('[models]\nhigh = "openrouter:openai/gpt-6-sol"\n')
    config = load_config(git_repo, overrides=["models.low=openrouter:openai/gpt-6-luna"])
    wrote, io, _ = setup(["", SECRET, "", ""], tmp_path, config=config)
    assert wrote is True
    assert written(tmp_path).models == {"high": SUGGESTED_HIGH, "low": SUGGESTED_LOW}
    assert "Note: phil.toml sets models.high here, which overrides the global file." in io.lines
    assert not any("--set" in line for line in io.lines)


def test_a_repo_provider_does_not_become_the_default_provider(tmp_path, git_repo):
    (git_repo / "phil.toml").write_text('[models]\nhigh = "openai:gpt-x"\n')
    wrote, _, _ = setup(["", SECRET, "", ""], tmp_path, config=load_config(git_repo))
    assert wrote is True
    assert written(tmp_path).models["high"] == SUGGESTED_HIGH


def test_a_rerun_prefills_a_custom_provider(tmp_path):
    global_config_path().parent.mkdir(parents=True)
    global_config_path().write_text(
        '[models]\nhigh = "lab:big"\nlow = "lab:small"\n'
        '[providers.lab]\nkind = "openai"\nbase_url = "http://lab/v1"\napi_key_env = "LAB_API_KEY"\n'
    )
    wrote, io, _ = setup(["", "", "", "", SECRET, "", ""], tmp_path)
    assert wrote is True
    config = written(tmp_path)
    assert config.models == {"high": "lab:big", "low": "lab:small"}
    assert config.providers["lab"].base_url == "http://lab/v1"


def test_a_rerun_keeps_a_saved_lower_case_key_variable_but_a_typed_one_must_be_upper_case(tmp_path):
    global_config_path().parent.mkdir(parents=True)
    global_config_path().write_text(
        '[models]\nhigh = "lab:big"\nlow = "lab:small"\n'
        '[providers.lab]\nkind = "openai"\nbase_url = "http://lab/v1"\napi_key_env = "lab_key"\n'
    )
    # Enter at the variable prompt keeps the saved name, even though it isn't upper case.
    wrote, io, _ = setup(["", "", "", "", "", "", ""], tmp_path)
    assert wrote is True
    assert not any("isn't a valid variable name" in line for line in io.lines)
    assert written(tmp_path).providers["lab"].api_key_env == "lab_key"

    # Typing a different lower-case name is still refused.
    wrote, io, _ = setup(["", "", "", "other_key", "OTHER_KEY", "", "", ""], tmp_path)
    assert wrote is True
    assert any("isn't a valid variable name" in line for line in io.lines)
    assert written(tmp_path).providers["lab"].api_key_env == "OTHER_KEY"


# A key pasted at a non-secret prompt is never echoed, written or sent (final review A1/A2).

PLANTED = "sk-proj-TESTSECRET0123456789abcdefABCDEF"
KEY_WARNING = (
    "That looks like an API key — it isn't shown or saved here. "
    "Enter it at the key prompt (or phil keys set <provider>). "
    "If it's a model id, enter it as <provider>:<id>."
)


def assert_never_leaked(io, check):
    assert KEY_WARNING in io.lines
    assert all(PLANTED not in line for line in io.lines)
    if global_config_path().exists():
        assert PLANTED not in global_config_path().read_text()
    assert all(PLANTED not in repr(config.model_dump()) for config in check.configs)


def test_a_key_at_the_custom_key_variable_prompt_is_refused(tmp_path, memory_keyring):
    answers = ["Custom", "lab", "http://lab:8000/v1", PLANTED, "", SECRET, "big", "small"]
    wrote, io, check = setup(answers, tmp_path)
    assert wrote is True
    assert_never_leaked(io, check)
    assert written(tmp_path).providers["lab"].api_key_env == "LAB_API_KEY"
    assert memory_keyring.store == {(SERVICE, "LAB_API_KEY"): SECRET}


def test_a_key_at_the_custom_name_and_base_url_prompts_is_refused(tmp_path, memory_keyring):
    answers = ["Custom", PLANTED, "lab", PLANTED, "http://lab:8000/v1", "", SECRET, "big", "small"]
    wrote, io, check = setup(answers, tmp_path)
    assert wrote is True
    assert_never_leaked(io, check)
    assert io.lines.count(KEY_WARNING) == 2


def test_the_custom_key_variable_must_be_upper_case_and_is_not_echoed(tmp_path, memory_keyring):
    answers = ["Custom", "lab", "http://lab:8000/v1", "lab_key", "LAB_KEY", SECRET, "big", "small"]
    wrote, io, _ = setup(answers, tmp_path)
    assert wrote is True
    assert any("isn't a valid variable name" in line for line in io.lines)
    assert all("lab_key" not in line for line in io.lines)
    assert written(tmp_path).providers["lab"].api_key_env == "LAB_KEY"


@pytest.mark.parametrize(
    "answers",
    [
        ["OpenAI", SECRET, PLANTED, "gpt-x", "gpt-y"],  # the high model
        ["OpenAI", SECRET, "gpt-x", PLANTED, "gpt-y"],  # the low model
        ["OpenAI", SECRET, f"openai:{PLANTED}", "gpt-x", "gpt-y"],  # behind the provider prefix
    ],
)
def test_a_key_at_a_model_prompt_is_refused(tmp_path, memory_keyring, answers):
    wrote, io, check = setup(answers, tmp_path)
    assert wrote is True
    assert_never_leaked(io, check)
    assert written(tmp_path).models == {"high": "openai:gpt-x", "low": "openai:gpt-y"}


@pytest.mark.parametrize("typed", [PLANTED, f"openrouter:{PLANTED}"])
def test_a_key_at_the_openrouter_search_is_refused(tmp_path, memory_keyring, typed):
    wrote, io, check = setup(["", SECRET, typed, "", typed, ""], tmp_path)
    assert wrote is True
    assert_never_leaked(io, check)
    assert io.lines.count(KEY_WARNING) == 2
    assert "Fetching the OpenRouter model list…" not in io.lines  # nothing was searched
    assert written(tmp_path).models == {"high": SUGGESTED_HIGH, "low": SUGGESTED_LOW}


def test_a_key_when_choosing_a_model_after_a_failed_check_is_refused(tmp_path, memory_keyring):
    check = FakeCheck(bad={SUGGESTED_HIGH})
    answers = ["", SECRET, "", "", "Choose a different", PLANTED, "openai/gpt-6-sol"]
    wrote, io, check = setup(answers, tmp_path, check=check)
    assert wrote is True
    assert_never_leaked(io, check)
    assert written(tmp_path).models["high"] == "openrouter:openai/gpt-6-sol"
