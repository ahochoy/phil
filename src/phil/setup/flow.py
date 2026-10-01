"""The guided setup: provider, key, models, check, write (spec §3.3).

Keys go only to the keychain, through `set_key`; they are never said, written or logged."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from phil.agents.providers import ALIASES, is_known_provider, resolve_provider, split_model
from phil.config import REPO_SOURCE, ROLES, SET_SOURCE, PhilConfig, global_config_path
from phil.key_store import KeyStoreError, key_source, keychain_available, set_key
from phil.setup.catalog import CatalogModel, ollama_models, openrouter_catalog, search_openrouter
from phil.setup.io import SetupCancelled, SetupIO
from phil.setup.suggestions import SUGGESTIONS
from phil.setup.write import write_global_config

SETUP_TIERS = ("high", "low")
CUSTOM = "custom"
PROVIDER_CHOICES: list[tuple[str, str]] = [
    ("openrouter", "OpenRouter (recommended: one key for many models)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("google", "Google"),
    ("ollama", "Ollama (local)"),
    (CUSTOM, "Custom OpenAI-compatible"),
]
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")
VAR_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NEXT_STEPS = (
    "Next: `phil` starts a chat, `phil config` shows your settings, "
    "`phil keys list` shows where each key comes from."
)


class _Back(Exception):
    """Return to the provider choice."""


@dataclass
class _Provider:
    name: str  # the `provider:` prefix of its models
    api_key_env: str | None
    custom: bool = False  # a [providers.<name>] table setup writes
    fields: dict[str, object] = field(default_factory=dict)
    installed: list[str] = field(default_factory=list)  # Ollama's models


def _check_models(config: PhilConfig):
    from phil.agents.check import check_models

    return check_models(config, repo_root=Path.cwd())


def run_setup(
    io: SetupIO,
    *,
    config: PhilConfig,
    check: Callable[[PhilConfig], list] = _check_models,
    catalog: Callable[[], list[CatalogModel]] = openrouter_catalog,
    ollama: Callable[[str], list[str] | None] = ollama_models,
    write: Callable[..., None] = write_global_config,
    path: Path | None = None,
) -> bool:
    """Walk through setup and write the global config. True if it wrote the file, False if the
    user cancelled (Ctrl-C or end of input), in which case nothing is written."""
    try:
        provider = _provider_step(io, config, ollama)
        _key_step(io, provider)
        models_catalog = _LazyCatalog(catalog)
        models = {tier: _model_step(io, config, provider, tier, models_catalog) for tier in SETUP_TIERS}
        models = _check_step(io, config, provider, models, check, models_catalog)
        target = path or global_config_path()
        write(
            target,
            models=models,
            provider_name=provider.name if provider.custom else None,
            provider_fields=provider.fields if provider.custom else {},
        )
    except SetupCancelled:
        io.say("Setup cancelled; nothing was written.")
        return False
    _summarise(io, config, provider, models, target)
    return True


# 1. Provider


def _current_provider(config: PhilConfig) -> str | None:
    high = config.models.get("high")
    if not high:
        return None
    name = split_model(high)[0]
    return ALIASES.get(name, name)


def _provider_step(io: SetupIO, config: PhilConfig, ollama: Callable[[str], list[str] | None]) -> _Provider:
    names = [name for name, _ in PROVIDER_CHOICES]
    current = _current_provider(config)
    custom_current = current if current is not None and not is_known_provider(current) else None
    if current in names:
        default = names.index(current)
    elif custom_current in config.providers:
        default = names.index(CUSTOM)
    else:
        default = 0
    while True:
        index = io.choose("Which provider?", [label for _, label in PROVIDER_CHOICES], default=default)
        name = names[index]
        try:
            if name == "ollama":
                return _ollama_provider(io, config, ollama)
            if name == CUSTOM:
                return _custom_provider(io, config, custom_current)
        except _Back:
            default = index
            continue
        return _Provider(name, resolve_provider(config, name).api_key_env)


def _ollama_provider(io: SetupIO, config: PhilConfig, ollama: Callable[[str], list[str] | None]) -> _Provider:
    base_url = resolve_provider(config, "ollama").base_url or "http://localhost:11434/v1"
    while True:
        installed = ollama(base_url)
        if installed:
            return _Provider("ollama", resolve_provider(config, "ollama").api_key_env, installed=installed)
        if installed is None:
            io.say(f"Couldn't reach Ollama at {base_url}. Start it with `ollama serve`, then retry.")
        else:
            io.say("Ollama has no models installed. Pull one with `ollama pull <model>`, then retry.")
        if io.choose("Retry, or go back to the providers?", ["Retry", "Back"]) == 1:
            raise _Back


def _custom_provider(io: SetupIO, config: PhilConfig, current: str | None) -> _Provider:
    while True:
        name = io.ask("Provider name (lowercase, e.g. lab)", default=current)
        if not NAME_PATTERN.fullmatch(name):
            io.say(
                f'"{name}" isn\'t a valid provider name: use lowercase letters, digits, "-" and "_", '
                "starting with a letter."
            )
        elif is_known_provider(name):
            io.say(f"{name} is a built-in provider: choose it from the list, or pick another name.")
        else:
            break
    entry = config.providers.get(name)
    while True:
        base_url = io.ask("Base URL (e.g. http://localhost:8000/v1)", default=entry.base_url if entry else None)
        if base_url:
            break
        io.say("Enter the server's OpenAI-compatible base URL.")
    if entry is not None:
        default_var = entry.api_key_env or "none"
    else:
        default_var = f"{name.upper().replace('-', '_')}_API_KEY"
    while True:
        var = io.ask("Environment variable for its API key (none for no key)", default=default_var)
        if var.lower() == "none":
            api_key_env = None
            break
        if VAR_PATTERN.fullmatch(var):
            api_key_env = var
            break
        io.say(f'"{var}" isn\'t a valid variable name.')
    kind = entry.kind if entry is not None and entry.kind else "openai"
    fields: dict[str, object] = {"kind": kind, "base_url": base_url, "api_key_env": api_key_env}
    return _Provider(name, api_key_env, custom=True, fields=fields)


# 2. Key


def _key_step(io: SetupIO, provider: _Provider) -> None:
    var = provider.api_key_env
    if var is None:
        return
    source = key_source(var)
    if source is not None:
        io.say(f"Using {var} from {source}.")
        if source != "keychain":
            return
        if io.choose(f"Keep the saved {var}, or replace it?", ["Keep it", "Replace it"]) == 0:
            return
    elif not keychain_available():
        io.say(f"No keychain is available here; export {var} instead.")
        return
    value = io.secret(f"{var} (input hidden)")
    if not value:
        io.say(f"No key entered. Export {var}, or run `phil keys set {provider.name}` later.")
        return
    try:
        set_key(var, value)
    except KeyStoreError as exc:
        io.say(str(exc))
        return
    io.say(f"Saved {var} in the keychain.")


# 3. Models


class _LazyCatalog:
    """The OpenRouter catalog, loaded on first use only (and only once)."""

    def __init__(self, load: Callable[[], list[CatalogModel]]) -> None:
        self._load = load
        self._models: list[CatalogModel] | None = None

    def get(self) -> list[CatalogModel]:
        if self._models is None:
            try:
                self._models = list(self._load())
            except Exception:
                self._models = []
        return self._models


def _default_model(config: PhilConfig, provider: _Provider, tier: str) -> str | None:
    """The current model on a rerun (when it is this provider's), else the suggestion."""
    current = config.models.get(tier)
    if current and _current_provider_of(current) == provider.name:
        return current
    return SUGGESTIONS.get(provider.name, {}).get(tier)


def _current_provider_of(model: str) -> str:
    name = split_model(model)[0]
    return ALIASES.get(name, name)


def _model_step(
    io: SetupIO, config: PhilConfig, provider: _Provider, tier: str, catalog: _LazyCatalog,
    *, default: str | None = None, use_default: bool = True,
) -> str:
    if use_default:
        default = _default_model(config, provider, tier)
    if provider.name == "ollama":
        return _ollama_model(io, provider, tier, default)
    if provider.name == "openrouter":
        return _openrouter_model(io, tier, default, catalog)
    prefix = f"{provider.name}:"
    while True:
        answer = io.ask(f"{tier} model", default=default)
        if answer:
            return answer if answer.startswith(prefix) else prefix + answer
        io.say(f"Enter a model id, e.g. {prefix}<model>.")


def _ollama_model(io: SetupIO, provider: _Provider, tier: str, default: str | None) -> str:
    names = provider.installed
    current = default.removeprefix("ollama:") if default else None
    index = io.choose(f"{tier} model", names, default=names.index(current) if current in names else 0)
    return f"ollama:{names[index]}"


def _usd(value: float) -> str:
    return f"${value:.2f}" if value == 0 or value >= 0.1 else f"${value:.3f}"


def _describe(model: CatalogModel) -> str:
    if model.input_per_mtok is None or model.output_per_mtok is None:
        return f"{model.id}  (no price listed)"
    return f"{model.id}  {_usd(model.input_per_mtok)}/{_usd(model.output_per_mtok)} per M"


def _openrouter_model(io: SetupIO, tier: str, default: str | None, catalog: _LazyCatalog) -> str:
    while True:
        answer = io.ask(f"{tier} model", default=default)
        if not answer:
            io.say("Enter an OpenRouter model id, or part of one to search.")
            continue
        if answer == default:
            return answer
        model_id = answer.removeprefix("openrouter:")
        models = catalog.get()
        if not models:
            io.say("The OpenRouter model list isn't available right now; using the id as typed.")
            return f"openrouter:{model_id}"
        if any(model.id == model_id for model in models):
            return f"openrouter:{model_id}"
        matches = search_openrouter(model_id, models)
        if not matches:
            io.say(f'No OpenRouter model matches "{model_id}". Try part of an id, e.g. "claude" or "gemini".')
            continue
        index = io.choose(
            f'OpenRouter models matching "{model_id}" (input/output price):',
            [_describe(model) for model in matches] + ["Type again"],
        )
        if index < len(matches):
            return f"openrouter:{matches[index].id}"


# 4. Check


def _candidate(config: PhilConfig, provider: _Provider, models: dict[str, str]) -> PhilConfig:
    """The config the check runs on: the current settings with setup's tiers and provider entry,
    and without role keys under [models], which would hide the tiers being set up."""
    data = config.model_dump(exclude_unset=True)
    data["models"] = {key: value for key, value in config.models.items() if key not in ROLES} | models
    if provider.custom:
        providers = dict(data.get("providers", {}))
        entry = dict(providers.get(provider.name, {}))
        for name, value in provider.fields.items():
            if value is None:
                entry.pop(name, None)
            else:
                entry[name] = value
        providers[provider.name] = entry
        data["providers"] = providers
    return PhilConfig.model_validate(data)


def _result_line(result) -> str:
    if result.ok:
        return f"✓ {result.label}  {result.model}  {result.seconds:.1f}s"
    return f"✗ {result.label}  {result.model}  {result.detail}"


def _check_step(
    io: SetupIO,
    config: PhilConfig,
    provider: _Provider,
    models: dict[str, str],
    check: Callable[[PhilConfig], list],
    catalog: _LazyCatalog,
) -> dict[str, str]:
    models = dict(models)
    while True:
        io.say("Checking the models (one short call each)...")
        results = check(_candidate(config, provider, models))
        for result in results:
            io.say(_result_line(result))
        failed: list[str] = []
        for result in results:
            if not result.ok:
                failed += [tier for tier in result.label.split(", ") if tier in SETUP_TIERS and tier not in failed]
        changed = False
        for tier in failed:
            choice = io.choose(
                f"The {tier} model ({models[tier]}) failed the check.",
                [f"Choose a different {tier} model", "Keep it anyway"],
            )
            if choice == 0:
                suggestion = SUGGESTIONS.get(provider.name, {}).get(tier)
                fallback = suggestion if suggestion != models[tier] else None
                models[tier] = _model_step(
                    io, config, provider, tier, catalog, default=fallback, use_default=False
                )
                changed = True
        if not changed:
            return models


# 5. Summary


def _summarise(io: SetupIO, config: PhilConfig, provider: _Provider, models: dict[str, str], path: Path) -> None:
    io.say(f"Wrote models.high = {models['high']} and models.low = {models['low']} to {path}.")
    if provider.custom:
        written = ", ".join(f"{key} = {value}" for key, value in provider.fields.items() if value is not None)
        io.say(f"Also wrote [providers.{provider.name}]: {written}.")
    for key, model in config.models.items():
        if key in ROLES:
            source = config.sources.get(f"models.{key}", "config")
            io.say(f"Note: models.{key} = {model} (from {source}) still overrides its tier for {key}.")
    for tier in SETUP_TIERS:
        source = config.sources.get(f"models.{tier}")
        if source in (REPO_SOURCE, SET_SOURCE):
            io.say(f"Note: {source} sets models.{tier} here, which overrides the global file.")
    io.say(NEXT_STEPS)
