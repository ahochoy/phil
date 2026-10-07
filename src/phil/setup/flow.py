"""The guided setup: provider, key, models, check, write (spec §3.3).

Keys go only to the keychain, through `set_key`, and only once setup finishes writing the
config: every key entered along the way is held in memory (via `pending_keys`) so the model
and classifier checks can use it, but nothing is saved until the end. Cancelling at any step
(Ctrl-C or end of input) saves nothing and writes nothing. Keys are never said, written or
logged."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from tomlkit.exceptions import TOMLKitError

from phil.agents.providers import ALIASES, is_known_provider, resolve_provider, split_model
from phil.config import REPO_SOURCE, ROLES, PhilConfig, global_config_path
from phil.key_store import KeyStoreError, key_source, keychain_available, pending_keys, set_key
from phil.routing.jev import JevError, ping_jev
from phil.setup.catalog import CatalogModel, ollama_models, openrouter_catalog, search_openrouter
from phil.setup.guard import KEY_WARNING, looks_like_key
from phil.setup.io import SetupCancelled, SetupIO
from phil.setup.suggestions import SUGGESTIONS
from phil.setup.write import write_global_config

SETUP_TIERS = ("high", "low")
JEV_TYPESAFE = "typesafe:jev-latest"
JEV_OPENROUTER = "openrouter_decisions:typesafe/jev-1.13"
D1_OPENROUTER = "openrouter_decisions:liquid/d1"
# The classifier step's sources (spec 2026-10-06 §3.2): (label, model, key var, key provider name).
CLASSIFIER_SOURCES: list[tuple[str, str, str, str]] = [
    ("Jev through OpenRouter (recommended; uses your OpenRouter key)", JEV_OPENROUTER, "OPENROUTER_API_KEY", "openrouter"),
    ("Jev through TypeSafe (recommended without OpenRouter; needs TYPESAFE_API_KEY)", JEV_TYPESAFE, "TYPESAFE_API_KEY", "typesafe"),
    ("d1 through OpenRouter (experimental: routing thresholds were tuned for Jev)", D1_OPENROUTER, "OPENROUTER_API_KEY", "openrouter"),
]
LOW_MODEL = "Your low model"
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
VAR_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
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


def _ask(io: SetupIO, prompt: str, default: str | None = None) -> str:
    """`io.ask` for a non-secret answer: one that looks like an API key is refused, never echoed,
    and asked for again, so it can't reach the config file, a message or a check call."""
    while True:
        answer = io.ask(prompt, default=default)
        if not looks_like_key(answer):
            return answer
        io.say(KEY_WARNING)


def _check_models(config: PhilConfig):
    from phil.agents.check import check_models

    return check_models(config, repo_root=Path.cwd())


def _default_classifier_check(config: PhilConfig, model: str) -> str | None:
    """Pings the chosen decision model through its own provider with `ping_jev`; `None` if it
    answered, else the reason."""
    provider = resolve_provider(config, split_model(model)[0])
    try:
        ping_jev(provider, split_model(model)[1], timeout_s=config.routing.jev_timeout_s)
    except JevError as exc:
        return exc.reason
    return None


def run_setup(
    io: SetupIO,
    *,
    config: PhilConfig,
    check: Callable[[PhilConfig], list] = _check_models,
    catalog: Callable[[], list[CatalogModel]] = openrouter_catalog,
    ollama: Callable[[str], list[str] | None] = ollama_models,
    write: Callable[..., None] = write_global_config,
    path: Path | None = None,
    classifier_check: Callable[[str], str | None] | None = None,
) -> bool:
    """Walk through setup and write the global config. True if it wrote the file; False if the
    user cancelled (Ctrl-C or end of input, at a prompt or not) or the file couldn't be written.
    Every key entered along the way is held in memory only; cancelling, or a failed write,
    saves nothing and leaves the keychain untouched."""
    target = path or global_config_path()
    # Only the global file's own values are setup's to pre-fill: a repo's phil.toml or `--set`
    # values aren't what this rerun edits.
    saved = {
        tier: config.models[tier]
        for tier in SETUP_TIERS
        if tier in config.models and config.sources.get(f"models.{tier}") == str(target)
    }
    run_classifier_check = (
        classifier_check if classifier_check is not None else partial(_default_classifier_check, config)
    )
    pending: dict[str, str] = {}
    to_save: list[tuple[str, str]] = []
    try:
        provider = _provider_step(io, config, saved, ollama)
        key = _key_step(io, provider)
        if key is not None and provider.api_key_env is not None:
            pending[provider.api_key_env] = key
            to_save.append((provider.api_key_env, provider.name))
        models_catalog = _LazyCatalog(io, catalog)
        models = {tier: _model_step(io, saved, provider, tier, models_catalog) for tier in SETUP_TIERS}
        with pending_keys(pending):
            models = _check_step(io, config, saved, provider, models, check, models_catalog)
        models = models | _classifier_step(io, config, target, provider, run_classifier_check, pending, to_save)
    except (SetupCancelled, KeyboardInterrupt):
        io.say("Setup cancelled; nothing was saved.")
        return False
    try:
        write(
            target,
            models=models,
            provider_name=provider.name if provider.custom else None,
            provider_fields=provider.fields if provider.custom else {},
        )
    except (OSError, TOMLKitError) as exc:
        io.say(f"Couldn't write {target}: {_error_text(exc)}. Nothing was saved.")
        return False
    # The config is written before any key: `_summarise` says so first, then each pending key is
    # saved — so the user reads "wrote the config" before "saved the key", in that true order.
    _summarise(io, config, provider, models, target)
    _save_pending_keys(io, to_save, pending)
    return True


def _save_pending_keys(io: SetupIO, to_save: list[tuple[str, str]], pending: dict[str, str]) -> None:
    """Save every key setup collected, now that the config is written. A failure doesn't stop
    the others: the config is already written, so setup still reports success overall."""
    for var, provider_name in to_save:
        try:
            set_key(var, pending[var])
        except KeyStoreError as exc:
            reason = (
                type(exc.__cause__).__name__ if exc.__cause__ is not None else "no keychain is available here"
            )
            io.say(
                f"Couldn't save {var} to the keychain ({reason}). Export {var}, or run phil keys set {provider_name}."
            )
            continue
        io.say(f"Saved {var} for {provider_name} in the keychain.")


def _error_text(exc: Exception) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


# 1. Provider


def _provider_step(
    io: SetupIO, config: PhilConfig, saved: dict[str, str], ollama: Callable[[str], list[str] | None]
) -> _Provider:
    names = [name for name, _ in PROVIDER_CHOICES]
    current = _provider_of(saved["high"]) if "high" in saved else None
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
        name = _ask(io, "Provider name (lowercase, e.g. lab)", default=current)
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
        base_url = _ask(io, "Base URL (e.g. http://localhost:8000/v1)", default=entry.base_url if entry else None)
        if base_url:
            break
        io.say("Enter the server's OpenAI-compatible base URL.")
    if entry is not None:
        default_var = entry.api_key_env or "none"
    else:
        default_var = f"{name.upper().replace('-', '_')}_API_KEY"
    while True:
        var = _ask(io, "Environment variable for its API key (none for no key)", default=default_var)
        if var.lower() == "none":
            api_key_env = None
            break
        # The existing entry's own name is kept on a rerun, even if it predates the upper-case rule.
        if VAR_PATTERN.fullmatch(var) or (entry is not None and var == entry.api_key_env):
            api_key_env = var
            break
        # Not echoed: whatever was typed here might be part of a key.
        io.say('That isn\'t a valid variable name: use capital letters, digits and "_", starting with a letter.')
    kind = entry.kind if entry is not None and entry.kind else "openai"
    fields: dict[str, object] = {"kind": kind, "base_url": base_url, "api_key_env": api_key_env}
    return _Provider(name, api_key_env, custom=True, fields=fields)


# 2. Key


def _key_step(io: SetupIO, provider: _Provider, *, hint_if_empty: bool = True) -> str | None:
    """Make sure the provider's key can be found. Returns the entered key (held in memory,
    saved to the keychain only once setup finishes), or None if none was entered, the key was
    kept, or it comes from the environment. `hint_if_empty=False` leaves the "No key entered"
    advice to the caller (the classifier step offers its own choice instead)."""
    var = provider.api_key_env
    if var is None:
        return None
    source = key_source(var)
    if source is not None:
        io.say(f"Using {var} from {source}.")
        if source != "keychain":
            return None
        if io.choose(f"Keep the saved {var}, or replace it?", ["Keep it", "Replace it"]) == 0:
            return None
    elif not keychain_available():
        io.say(f"No keychain is available here; export {var} instead.")
        return None
    value = io.secret(f"{var} (input hidden)")
    if not value:
        if hint_if_empty:
            io.say(f"No key entered. Export {var}, or run `phil keys set {provider.name}` later.")
        return None
    io.say(f"Got it: {var} will be saved to the keychain when setup finishes.")
    return value


# 3. Models


class _LazyCatalog:
    """The OpenRouter catalog, loaded on first use only (and only once). A Ctrl-C while it
    loads propagates, and cancels setup."""

    def __init__(self, io: SetupIO, load: Callable[[], list[CatalogModel]]) -> None:
        self._io = io
        self._load = load
        self._models: list[CatalogModel] | None = None

    def get(self) -> list[CatalogModel]:
        if self._models is None:
            self._io.say("Fetching the OpenRouter model list…")
            try:
                self._models = list(self._load())
            except Exception:
                self._models = []
        return self._models


def _provider_of(model: str) -> str:
    name = split_model(model)[0]
    return ALIASES.get(name, name)


def _default_model(saved: dict[str, str], provider: _Provider, tier: str) -> str | None:
    """The global file's current model on a rerun (when it is this provider's), else the suggestion."""
    current = saved.get(tier)
    if current and _provider_of(current) == provider.name:
        return current
    return SUGGESTIONS.get(provider.name, {}).get(tier)


def _model_step(
    io: SetupIO, saved: dict[str, str], provider: _Provider, tier: str, catalog: _LazyCatalog,
    *, default: str | None = None, use_default: bool = True,
) -> str:
    if use_default:
        default = _default_model(saved, provider, tier)
    if provider.name == "ollama":
        return _ollama_model(io, provider, tier, default)
    if provider.name == "openrouter":
        return _openrouter_model(io, tier, default, catalog)
    prefix = f"{provider.name}:"
    while True:
        answer = _ask(io, f"{tier} model", default=default)
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
        answer = _ask(io, f"{tier} model", default=default)
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
        if matches:
            prompt = f'OpenRouter models matching "{model_id}" (input/output price):'
        else:
            prompt = f'No OpenRouter model matches "{model_id}".'
        options = [_describe(model) for model in matches] + [f"Use '{model_id}' as typed", "Type again"]
        index = io.choose(prompt, options)
        if index < len(matches):
            return f"openrouter:{matches[index].id}"
        if index == len(matches):
            return f"openrouter:{model_id}"


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


def _missing_key(provider: _Provider, result) -> bool:
    """The result failed because the provider's key can't be found, not because of the model."""
    var = provider.api_key_env
    return var is not None and f"needs {var}" in result.detail and key_source(var) is None


def _check_step(
    io: SetupIO,
    config: PhilConfig,
    saved: dict[str, str],
    provider: _Provider,
    models: dict[str, str],
    check: Callable[[PhilConfig], list],
    catalog: _LazyCatalog,
) -> dict[str, str]:
    from phil.agents.check import unused_tiers

    models = dict(models)
    while True:
        io.say("Checking the models (one short call each)...")
        candidate = _candidate(config, provider, models)
        results = check(candidate)
        for result in results:
            io.say(_result_line(result))
        for tier, model in unused_tiers(candidate):
            if tier in SETUP_TIERS:
                io.say(f"– {tier}  {model}  unused (no role maps to it)")
        failed: list[str] = []
        key_missing = False
        for result in results:
            if result.ok:
                continue
            if _missing_key(provider, result):
                key_missing = True  # another model wouldn't help: the key is what's missing
                continue
            failed += [tier for tier in result.label.split(", ") if tier in SETUP_TIERS and tier not in failed]
        if key_missing:
            var = provider.api_key_env
            io.say(f"{var} isn't set: export {var}, or run `phil keys set {provider.name}`.")
            if io.choose("Keep these models anyway?", ["Keep them anyway", "Cancel setup"]) == 1:
                raise SetupCancelled
        changed = False
        for tier in failed:
            choice = io.choose(
                f"The {tier} model ({models[tier]}) failed the check.",
                [f"Choose a different {tier} model", "Keep it anyway"],
            )
            if choice == 0:
                suggestion = SUGGESTIONS.get(provider.name, {}).get(tier)
                fallback = suggestion if suggestion != models[tier] else None
                models[tier] = _model_step(io, saved, provider, tier, catalog, default=fallback, use_default=False)
                changed = True
        if not changed:
            return models


def _classifier_step(
    io: SetupIO,
    config: PhilConfig,
    target: Path,
    provider: _Provider,
    classifier_check: Callable[[str], str | None],
    pending: dict[str, str],
    to_save: list[tuple[str, str]],
) -> dict[str, str]:
    """Offer a decision model for routing (spec 2026-10-06 §3.2). Jev is recommended: through
    OpenRouter when that's the main provider (its key is reused), else through TypeSafe. Empty
    when the user keeps routing with the low model; `{"classifier": <model>}` when one is chosen
    and kept. A key entered here is held in memory like every other key, saved only at the end."""
    current = config.models.get("classifier") if config.sources.get("models.classifier") == str(target) else None
    labels = ([f"Keep {current}"] if current else []) + [label for label, *_ in CLASSIFIER_SOURCES] + [LOW_MODEL]
    offset = 1 if current else 0
    default = 0 if current else (0 if provider.name == "openrouter" else 1)
    index = io.choose("Route requests with a fast classifier?", labels, default=default)
    choice = labels[index]
    if current and index == 0:
        # Keeping it writes nothing: write_global_config leaves the file's other keys as they are.
        return {}

    def use_low_model() -> dict[str, str]:
        # On a rerun, writing no classifier leaves the current one in the file: say so.
        if current:
            io.say(f"models.classifier = {current} stays in {target}; remove it there to route with your low model.")
        return {}

    if choice == LOW_MODEL:
        return use_low_model()
    _, model, var, key_provider = CLASSIFIER_SOURCES[index - offset]
    # A key already entered, stored or exported is reused (spec §3.2): ask only when there's none.
    if var not in pending:
        source = key_source(var)
        if source is not None:
            io.say(f"Using {var} from {source}.")
        else:
            key = _key_step(io, _Provider(key_provider, var), hint_if_empty=False)
            if key is not None:
                pending[var] = key
                to_save.append((var, key_provider))
    if var not in pending and key_source(var) is None:
        # Nothing to check with: a ping could only fail for want of the key.
        if io.choose(f"No {var} yet.", ["Use your low model for now", f"Keep {model} and set the key later"]) == 0:
            return use_low_model()
        io.say(f"Set {var} with `phil keys set {key_provider}` or export it; routing uses your low model until then.")
        return {"classifier": model}
    with pending_keys(pending):
        reason = classifier_check(model)
    if reason is not None:
        io.say(f"✗ classifier  {model}  {reason}")
        if io.choose(f"{model} failed the check.", ["Use your low model instead", f"Keep {model} anyway"]) == 0:
            return use_low_model()
    else:
        io.say(f"✓ classifier  {model}")
    return {"classifier": model}


# 5. Summary


def _summarise(io: SetupIO, config: PhilConfig, provider: _Provider, models: dict[str, str], path: Path) -> None:
    classifier = f" and models.classifier = {models['classifier']}" if "classifier" in models else ""
    io.say(f"Wrote models.high = {models['high']} and models.low = {models['low']}{classifier} to {path}.")
    if provider.custom:
        written = ", ".join(f"{key} = {value}" for key, value in provider.fields.items() if value is not None)
        io.say(f"Also wrote [providers.{provider.name}]: {written}.")
    for key, model in config.models.items():
        if key in ROLES:
            source = config.sources.get(f"models.{key}", "config")
            io.say(f"Note: models.{key} = {model} (from {source}) still overrides its tier for {key}.")
    for tier in SETUP_TIERS:
        # `--set` lasts one command, so only a repo's phil.toml keeps overriding what was written.
        if config.sources.get(f"models.{tier}") == REPO_SOURCE:
            io.say(f"Note: {REPO_SOURCE} sets models.{tier} here, which overrides the global file.")
    io.say(NEXT_STEPS)
