"""Model providers: which SDK builds a `provider:model` string, where it points, which key it reads.

Built-in providers can be overridden field by field, and new ones added, under `[providers.<name>]`.
Provider packages are imported only when a model of that kind is built."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from phil.config import ConfigError

if TYPE_CHECKING:
    from phil.config import PhilConfig

# A placeholder key for keyless local servers (Ollama, llama.cpp, ...), which ignore it; the
# OpenAI-style SDKs refuse to build without some key.
NO_KEY = "not-needed"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: str  # "openai" (OpenAI-compatible), "anthropic", "google" or "openrouter"
    base_url: str | None
    api_key_env: str | None  # None: no key is sent or checked
    input_per_mtok: float | None  # USD per million tokens
    output_per_mtok: float | None


BUILTIN_PROVIDERS: dict[str, ProviderSpec] = {
    "openrouter": ProviderSpec("openrouter", "openrouter", None, "OPENROUTER_API_KEY", None, None),
    "openai": ProviderSpec("openai", "openai", None, "OPENAI_API_KEY", None, None),
    "anthropic": ProviderSpec("anthropic", "anthropic", None, "ANTHROPIC_API_KEY", None, None),
    "google": ProviderSpec("google", "google", None, "GOOGLE_API_KEY", None, None),
    "ollama": ProviderSpec("ollama", "openai", "http://localhost:11434/v1", None, 0.0, 0.0),
}
# Older names kept working for existing configs.
ALIASES = {"google_genai": "google"}


class UnknownProvider(ConfigError):
    def __init__(self, name: str, *, where: str | None = None, model: str | None = None) -> None:
        self.name = name
        self.where = where
        self.model = model
        located = f" in {where} model \"{model}\"" if where is not None and model is not None else ""
        super().__init__(
            f'Unknown provider "{name}"{located}. Add [providers.{name}] to ~/.phil/config.toml or phil.toml.'
        )


def split_model(model: str) -> tuple[str, str]:
    """`provider:model` split on the first `:` (the model name may contain more, e.g. `qwen3:32b`)."""
    provider, _, name = model.partition(":")
    return provider, name


def resolve_provider(config: "PhilConfig", name: str) -> ProviderSpec:
    """The provider `name` names: a built-in (or alias) with any `[providers.<name>]` fields laid over
    it, or a custom `[providers.<name>]` entry. Raises `UnknownProvider` otherwise."""
    canonical = ALIASES.get(name, name)
    entry = config.providers.get(canonical)
    if entry is None and canonical != name:
        entry = config.providers.get(name)
    builtin = BUILTIN_PROVIDERS.get(canonical)
    if builtin is None and entry is None:
        raise UnknownProvider(name)
    fields = {} if builtin is None else {
        "kind": builtin.kind,
        "base_url": builtin.base_url,
        "api_key_env": builtin.api_key_env,
        "input_per_mtok": builtin.input_per_mtok,
        "output_per_mtok": builtin.output_per_mtok,
    }
    if entry is not None:
        fields |= {field: getattr(entry, field) for field in entry.model_fields_set}
    if not fields.get("kind"):  # config validation requires it for custom entries; belt and braces
        raise UnknownProvider(name)
    return ProviderSpec(
        name=canonical,
        kind=fields["kind"],
        base_url=fields.get("base_url"),
        api_key_env=fields.get("api_key_env"),
        input_per_mtok=fields.get("input_per_mtok"),
        output_per_mtok=fields.get("output_per_mtok"),
    )


def provider_for_model(config: "PhilConfig", model: str, where: str) -> ProviderSpec:
    """`resolve_provider` for a model string, naming the role or tier (`where`) in an unknown-provider error."""
    name = split_model(model)[0]
    try:
        return resolve_provider(config, name)
    except UnknownProvider:
        raise UnknownProvider(name, where=where, model=model) from None


def is_known_provider(name: str) -> bool:
    return name in BUILTIN_PROVIDERS or name in ALIASES


def estimate_cost(spec: ProviderSpec, input_tokens: int, output_tokens: int) -> float | None:
    """The call's cost from the provider's own prices, or `None` unless both prices are set."""
    if spec.input_per_mtok is None or spec.output_per_mtok is None:
        return None
    return input_tokens / 1e6 * spec.input_per_mtok + output_tokens / 1e6 * spec.output_per_mtok


def build_chat_model(
    spec: ProviderSpec, model_name: str, timeout_s: int, environ: Mapping[str, str] = os.environ
) -> Any:
    """The LangChain chat model for `model_name` on `spec`: every call capped at `timeout_s` (in the
    SDK's own units) and the SDK's own retries off, so Phil's retry middleware is the only retry policy."""
    key = environ.get(spec.api_key_env) if spec.api_key_env else None
    if spec.kind == "openrouter":
        from langchain.chat_models import init_chat_model

        # ChatOpenRouter's `timeout` (request_timeout) is in milliseconds.
        kwargs: dict[str, Any] = {"timeout": timeout_s * 1000, "max_retries": 0}
        if key:
            kwargs["api_key"] = key
        if spec.base_url:
            kwargs["openrouter_api_base"] = spec.base_url
        return init_chat_model(f"openrouter:{model_name}", **kwargs)
    if spec.kind == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name, base_url=spec.base_url, api_key=key or NO_KEY, timeout=timeout_s, max_retries=0
        )
    if spec.kind == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name, base_url=spec.base_url, api_key=key or NO_KEY, timeout=timeout_s, max_retries=0
        )
    if spec.kind == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # The Google SDK reads max_retries=0 as "use its default" (5 retries); 1 is a single attempt.
        return ChatGoogleGenerativeAI(
            model=model_name, base_url=spec.base_url, google_api_key=key or NO_KEY, timeout=timeout_s, max_retries=1
        )
    raise ConfigError(f"Unknown provider kind {spec.kind!r} for provider {spec.name!r}")
