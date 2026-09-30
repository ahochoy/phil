import subprocess
import sys

import pytest

from phil.agents.providers import (
    ALIASES,
    BUILTIN_PROVIDERS,
    ProviderSpec,
    UnknownProvider,
    build_chat_model,
    resolve_provider,
    split_model,
)
from phil.config import ConfigError, PhilConfig, load_config

FAKE_ENVIRON = {
    "OPENROUTER_API_KEY": "dummy-openrouter",
    "OPENAI_API_KEY": "dummy-openai",
    "ANTHROPIC_API_KEY": "dummy-anthropic",
    "GOOGLE_API_KEY": "dummy-google",
}


def _secret(value) -> str | None:
    return value.get_secret_value() if value is not None else None


def test_importing_providers_does_not_load_llm_stack():
    code = (
        "import sys, phil.agents.providers; "
        "heavy = ('deepagents', 'langchain', 'langchain_core', 'langgraph', 'langchain_openrouter', "
        "'langchain_openai', 'langchain_anthropic', 'langchain_google_genai'); "
        "print(','.join(m for m in heavy if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_builtins_resolve():
    config = PhilConfig()
    assert resolve_provider(config, "openrouter") == ProviderSpec(
        "openrouter", "openrouter", None, "OPENROUTER_API_KEY", None, None
    )
    assert resolve_provider(config, "openai") == ProviderSpec("openai", "openai", None, "OPENAI_API_KEY", None, None)
    assert resolve_provider(config, "anthropic") == ProviderSpec(
        "anthropic", "anthropic", None, "ANTHROPIC_API_KEY", None, None
    )
    assert resolve_provider(config, "google") == ProviderSpec("google", "google", None, "GOOGLE_API_KEY", None, None)
    assert resolve_provider(config, "ollama") == ProviderSpec(
        "ollama", "openai", "http://localhost:11434/v1", None, 0.0, 0.0
    )
    assert set(BUILTIN_PROVIDERS) == {"openrouter", "openai", "anthropic", "google", "ollama"}


def test_google_genai_is_an_alias_of_google():
    assert ALIASES == {"google_genai": "google"}
    assert resolve_provider(PhilConfig(), "google_genai") == BUILTIN_PROVIDERS["google"]


def test_a_user_entry_overrides_only_the_fields_it_sets(tmp_path):
    (tmp_path / "phil.toml").write_text('[providers.ollama]\nbase_url = "http://box:11434/v1"\n')
    spec = resolve_provider(load_config(tmp_path), "ollama")
    assert spec == ProviderSpec("ollama", "openai", "http://box:11434/v1", None, 0.0, 0.0)


def test_a_custom_provider_resolves_from_its_entry(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[providers.lab]\nkind = "openai"\nbase_url = "http://lab:8000/v1"\napi_key_env = "LAB_KEY"\n'
        "input_per_mtok = 1.5\noutput_per_mtok = 6.0\n"
    )
    spec = resolve_provider(load_config(tmp_path), "lab")
    assert spec == ProviderSpec("lab", "openai", "http://lab:8000/v1", "LAB_KEY", 1.5, 6.0)


def test_a_custom_provider_needs_a_kind(tmp_path):
    (tmp_path / "phil.toml").write_text('[providers.lab]\nbase_url = "http://lab:8000/v1"\n')
    with pytest.raises(ConfigError, match=r'Invalid phil\.toml: providers.*\[providers\.lab\] needs a kind'):
        load_config(tmp_path)


def test_an_unknown_kind_is_rejected(tmp_path):
    (tmp_path / "phil.toml").write_text('[providers.lab]\nkind = "bedrock"\n')
    with pytest.raises(ConfigError, match=r"providers\.lab\.kind"):
        load_config(tmp_path)


def test_provider_merge_across_global_and_repo_keeps_every_field_and_its_source(tmp_path):
    from phil.config import global_config_path

    global_path = global_config_path()
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text('[providers.lab]\nkind = "openai"\nbase_url = "http://lab:8000/v1"\n')
    (tmp_path / "phil.toml").write_text('[providers.lab]\napi_key_env = "LAB_KEY"\n')
    config = load_config(tmp_path)
    assert resolve_provider(config, "lab") == ProviderSpec("lab", "openai", "http://lab:8000/v1", "LAB_KEY", None, None)
    assert config.sources["providers.lab.kind"] == str(global_path)
    assert config.sources["providers.lab.base_url"] == str(global_path)
    assert config.sources["providers.lab.api_key_env"] == "phil.toml"


def test_an_unknown_provider_raises_with_the_exact_message():
    with pytest.raises(UnknownProvider) as excinfo:
        resolve_provider(PhilConfig(), "nowhere")
    assert isinstance(excinfo.value, ConfigError)
    assert excinfo.value.name == "nowhere"
    assert str(excinfo.value) == (
        'Unknown provider "nowhere". Add [providers.nowhere] to ~/.phil/config.toml or phil.toml.'
    )
    located = UnknownProvider("nowhere", where="high", model="nowhere:big")
    assert str(located) == (
        'Unknown provider "nowhere" in high model "nowhere:big". '
        "Add [providers.nowhere] to ~/.phil/config.toml or phil.toml."
    )


def test_split_model_splits_on_the_first_colon():
    assert split_model("openrouter:openai/gpt-6-sol") == ("openrouter", "openai/gpt-6-sol")
    assert split_model("ollama:qwen3:32b") == ("ollama", "qwen3:32b")


# --- model construction: attributes only, no network --------------------------------------


def test_openai_kind_builds_chat_openai():
    pytest.importorskip("langchain_openai")
    model = build_chat_model(BUILTIN_PROVIDERS["openai"], "gpt-5-mini", 42, environ=FAKE_ENVIRON)
    assert type(model).__name__ == "ChatOpenAI"
    assert model.model_name == "gpt-5-mini"
    assert _secret(model.openai_api_key) == "dummy-openai"
    assert model.request_timeout == 42
    assert model.max_retries == 0


def test_ollama_builds_chat_openai_at_its_base_url_without_a_key():
    pytest.importorskip("langchain_openai")
    model = build_chat_model(BUILTIN_PROVIDERS["ollama"], "qwen3:32b", 90, environ={})
    assert type(model).__name__ == "ChatOpenAI"
    assert model.model_name == "qwen3:32b"
    assert model.openai_api_base == "http://localhost:11434/v1"
    assert _secret(model.openai_api_key) == "not-needed"
    assert model.request_timeout == 90
    assert model.max_retries == 0


def test_a_custom_openai_compatible_provider_uses_its_base_url_and_key():
    pytest.importorskip("langchain_openai")
    spec = ProviderSpec("lab", "openai", "http://lab:8000/v1", "LAB_KEY", None, None)
    model = build_chat_model(spec, "llama", 30, environ={"LAB_KEY": "lab-secret"})
    assert model.openai_api_base == "http://lab:8000/v1"
    assert _secret(model.openai_api_key) == "lab-secret"


def test_anthropic_kind_builds_chat_anthropic():
    pytest.importorskip("langchain_anthropic")
    spec = ProviderSpec("proxy", "anthropic", "http://proxy:9000", "ANTHROPIC_API_KEY", None, None)
    model = build_chat_model(spec, "claude-sonnet-5", 42, environ=FAKE_ENVIRON)
    assert type(model).__name__ == "ChatAnthropic"
    assert model.model == "claude-sonnet-5"
    assert model.anthropic_api_url == "http://proxy:9000"
    assert _secret(model.anthropic_api_key) == "dummy-anthropic"
    assert model.default_request_timeout == 42
    assert model.max_retries == 0


def test_google_kind_builds_chat_google_generative_ai():
    pytest.importorskip("langchain_google_genai")
    model = build_chat_model(BUILTIN_PROVIDERS["google"], "gemini-2.5-flash", 42, environ=FAKE_ENVIRON)
    assert type(model).__name__ == "ChatGoogleGenerativeAI"
    assert model.model == "gemini-2.5-flash"
    assert _secret(model.google_api_key) == "dummy-google"
    assert model.timeout == 42
    # The Google SDK reads max_retries=0 as "use its default" (5 retries); 1 means a single
    # attempt, i.e. no SDK retries.
    assert model.max_retries == 1


def test_openrouter_kind_uses_a_millisecond_timeout_and_no_sdk_retries():
    model = build_chat_model(BUILTIN_PROVIDERS["openrouter"], "openai/gpt-6-luna", 180, environ=FAKE_ENVIRON)
    assert type(model).__name__ == "ChatOpenRouter"
    assert model.model_name == "openai/gpt-6-luna"
    assert _secret(model.openrouter_api_key) == "dummy-openrouter"
    assert model.request_timeout == 180_000
    assert model.max_retries == 0


def test_openrouter_sdk_client_does_not_retry():
    # ChatOpenRouter leaves the SDK's retry_config UNSET when max_retries == 0, and the SDK then
    # falls back to retrying 5XX and connection errors with backoff for up to an hour.
    from openrouter.types import UNSET

    model = build_chat_model(BUILTIN_PROVIDERS["openrouter"], "openai/gpt-6-luna", 180, environ=FAKE_ENVIRON)
    for configuration in (model.client.sdk_configuration, model.client.chat.sdk_configuration):
        retry_config = configuration.retry_config
        assert retry_config is not UNSET
        assert retry_config.strategy == "none"
        assert retry_config.retry_connection_errors is False


@pytest.mark.parametrize("environ", [{}, {"OPENAI_API_KEY": ""}])
def test_a_keyed_provider_without_its_key_refuses_to_build(environ):
    with pytest.raises(ConfigError) as excinfo:
        build_chat_model(BUILTIN_PROVIDERS["openai"], "gpt-5-mini", 42, environ=environ, used_by=("critic",))
    assert str(excinfo.value) == "openai needs OPENAI_API_KEY (used by critic)."


def test_a_store_only_key_builds_an_openai_model(monkeypatch):
    pytest.importorskip("langchain_openai")
    from phil.credentials import set_key

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    model = build_chat_model(BUILTIN_PROVIDERS["openai"], "gpt-5-mini", 42)
    assert model.openai_api_key.get_secret_value() == "sk-TESTSECRET-stored"


def test_the_worker_build_path_finds_a_store_only_key(monkeypatch):
    # `phil.agents.factory.chat_model` is what `build_agent` calls for every real run and chat
    # call (including in a background worker); it calls `build_chat_model` with no `environ`.
    pytest.importorskip("langchain_openai")
    from phil.agents.factory import chat_model
    from phil.credentials import set_key

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    model = chat_model("openai:gpt-5-mini", 42)
    assert model.openai_api_key.get_secret_value() == "sk-TESTSECRET-stored"


def test_a_missing_key_message_without_roles():
    with pytest.raises(ConfigError, match=r"^anthropic needs ANTHROPIC_API_KEY\.$"):
        build_chat_model(BUILTIN_PROVIDERS["anthropic"], "claude-sonnet-5", 42, environ={})


def test_an_empty_api_key_env_is_rejected(tmp_path):
    (tmp_path / "phil.toml").write_text('[providers.lab]\nkind = "openai"\napi_key_env = ""\n')
    with pytest.raises(ConfigError, match=r"providers\.lab\.api_key_env"):
        load_config(tmp_path)


def test_a_provider_and_its_alias_cannot_both_be_defined(tmp_path):
    (tmp_path / "phil.toml").write_text(
        '[providers.google]\nbase_url = "http://a"\n[providers.google_genai]\nbase_url = "http://b"\n'
    )
    with pytest.raises(ConfigError, match=r"\[providers\.google\] and \[providers\.google_genai\] both configure"):
        load_config(tmp_path)
