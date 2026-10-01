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

    def __init__(self, bad=()):
        self.bad = set(bad)
        self.configs = []

    def __call__(self, config):
        self.configs.append(config)
        labels: dict[str, list[str]] = {}
        for tier in ("high", "low"):
            labels.setdefault(config.models[tier], []).append(tier)
        return [
            CheckResult(", ".join(tiers), model, model not in self.bad, 0.1, "no" if model in self.bad else "")
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


def setup(answers, tmp_path, *, check=None, ollama=None, config=None):
    io = ScriptedSetupIO(answers)
    check = check or FakeCheck()
    wrote = run_setup(
        io,
        config=config or load_config(tmp_path),
        check=check,
        catalog=lambda: CATALOG,
        ollama=ollama or FakeOllama(),
    )
    return wrote, io, check


def written(tmp_path):
    return load_config(tmp_path)


def test_a_fresh_openrouter_setup_stores_the_key_writes_the_tiers_and_checks_once(tmp_path, memory_keyring):
    wrote, io, check = setup(["", SECRET, "", ""], tmp_path)
    assert wrote is True
    assert memory_keyring.store == {(SERVICE, "OPENROUTER_API_KEY"): SECRET}
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


def test_no_keychain_says_so_and_carries_on(tmp_path, monkeypatch):
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    keyring.set_keyring(FailKeyring())
    wrote, io, _ = setup(["", "", ""], tmp_path)  # no key is asked for: there is nowhere to keep it
    assert wrote is True
    assert "No keychain is available here; export OPENROUTER_API_KEY instead." in io.lines
    assert not [kind for kind, _ in io.prompts if kind == "secret"]


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


@pytest.mark.parametrize(
    "answers",
    [
        [],  # at the provider choice
        [""],  # at the key prompt, before set_key
        ["", SECRET],  # at the high model
        ["", SECRET, ""],  # at the low model
        ["", SECRET, "", ""],  # at the failed-check choice
        ["Custom", "lab"],  # inside the custom provider questions
        ["Ollama"],  # at the unreachable-Ollama choice
    ],
)
def test_a_cancel_at_any_step_writes_nothing(tmp_path, memory_keyring, answers):
    check = FakeCheck(bad={SUGGESTED_HIGH})
    wrote, io, _ = setup(answers, tmp_path, check=check, ollama=FakeOllama(None))
    assert wrote is False
    assert not global_config_path().exists()
    assert any("nothing was written" in line for line in io.lines)
    if len(answers) < 2:
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
