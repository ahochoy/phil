import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr, ValidationError, field_validator

from phil.store.paths import phil_home
from phil.tomlw import dump_toml

ROLES = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")
TIERS = ("high", "low", "classifier")
# Tier each role resolves through when it has no model of its own (and [tiers] doesn't remap it).
DEFAULT_TIERS: dict[str, str] = {
    "architect": "high",
    "critic": "high",
    "reviewer": "high",
    "orchestrator": "low",
    "implementer": "low",
    "tester": "low",
}
# API-key environment variable each model provider reads. Providers not listed (a local server,
# a custom endpoint) are not checked.
PROVIDER_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}

# Roles the chat calls; `phil` checks these have models before the conversation starts.
CHAT_ROLES = ("orchestrator", "architect", "critic")
# Roles the run graph calls; `phil run` checks these have models before starting.
RUN_ROLES = ("implementer", "tester", "reviewer")
DEFAULT_BUDGETS = {"architect": 24_000, "tester": 48_000, "reviewer": 48_000}


class ConfigError(Exception):
    pass


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleBudget(_Section):
    max_input_tokens: int = 12_000


class RunConfig(_Section):
    tester_mode: Literal["run", "task+run"] = "run"
    max_attempts_per_phase: int = 3
    max_review_rounds: int = 2
    max_tokens: int = 1_500_000  # every model call counts (sub-agents, failed tries); cost is the main guard
    max_cost_usd: float = 2.0
    model_timeout_s: int = 180
    warn_at: float = 0.8

    @field_validator("model_timeout_s")
    @classmethod
    def _validate_model_timeout_s(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("run.model_timeout_s must be > 0")
        return value

    @field_validator("warn_at")
    @classmethod
    def _validate_warn_at(cls, value: float) -> float:
        if not 0 < value < 1:
            raise ValueError("run.warn_at must be between 0 and 1 (exclusive)")
        return value


class ShellConfig(_Section):
    allow: list[str] = [
        "pytest",
        "pytest *",
        "uv run pytest",
        "uv run pytest *",
        "npm test",
        "git status",
        "git diff",
        "git diff *",
    ]
    timeout_s: int = 300
    max_output_lines: int = 200
    pass_env: list[str] = []


class GitConfig(_Section):
    # Signing or hooks can make an unattended run pause for a human when a commit fails.
    sign_commits: bool | Literal["auto"] = "auto"  # "auto" follows the repo's commit.gpgsign
    run_hooks: bool = False  # run the repo's pre-commit / commit-msg hooks on Phil's commits


class ProjectConfig(_Section):
    test_cmd: str | None = None
    test_globs: list[str] = [
        "tests/*",
        "test/*",
        "test_*.py",
        "*_test.py",
        "*.test.ts",
        "*.spec.ts",
        "*_test.go",
        "conftest.py",
        "pytest.ini",
        "tox.ini",
        "jest.config.*",
        "vitest.config.*",
    ]


class PhilConfig(_Section):
    # No default model: each role's model is chosen explicitly in phil.toml, or through a tier.
    models: dict[str, str] = {}
    # Role -> tier, overriding DEFAULT_TIERS.
    tiers: dict[str, str] = {}
    budget: dict[str, RoleBudget] = {}
    run: RunConfig = RunConfig()
    shell: ShellConfig = ShellConfig()
    project: ProjectConfig = ProjectConfig()
    git: GitConfig = GitConfig()
    # Dotted leaf path -> the layer that set it: "default", the global file's path, "phil.toml" or "--set".
    _sources: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def sources(self) -> dict[str, str]:
        return self._sources

    @field_validator("models")
    @classmethod
    def _validate_model_roles(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - set(ROLES) - set(TIERS))
        if unknown:
            raise ValueError(
                f"Unknown key(s) in [models]: {unknown}. Valid roles: {list(ROLES)}, valid tiers: {list(TIERS)}"
            )
        return value

    @field_validator("tiers")
    @classmethod
    def _validate_tiers(cls, value: dict[str, str]) -> dict[str, str]:
        unknown_roles = sorted(set(value) - set(ROLES))
        if unknown_roles:
            raise ValueError(f"Unknown role(s) in [tiers]: {unknown_roles}. Valid roles: {list(ROLES)}")
        unknown_tiers = sorted(set(value.values()) - set(TIERS))
        if unknown_tiers:
            raise ValueError(f"Unknown tier(s) in [tiers]: {unknown_tiers}. Valid tiers: {list(TIERS)}")
        return value

    @field_validator("budget")
    @classmethod
    def _validate_budget_roles(cls, value: dict[str, RoleBudget]) -> dict[str, RoleBudget]:
        unknown = sorted(set(value) - set(ROLES))
        if unknown:
            raise ValueError(f"Unknown role(s) in [budget]: {unknown}. Valid roles: {list(ROLES)}")
        return value

    def tier_for(self, role: str) -> str:
        """The tier `role` resolves through: its `[tiers]` remap, else the default for that role."""
        return self.tiers.get(role, DEFAULT_TIERS.get(role, "low"))

    def tier_model(self, tier: str) -> str | None:
        """The model set for `tier` under `[models]`, or `None` if it isn't set.

        `classifier` falls back to the `low` model when it has none of its own."""
        if tier in self.models:
            return self.models[tier]
        if tier == "classifier":
            return self.models.get("low")
        return None

    def model_for(self, role: str) -> str:
        """The model for `role`: its own `[models]` key, else its tier's model. Raises if neither is set."""
        if role not in ROLES:
            raise ConfigError(f"Unknown role {role!r}. Valid roles: {list(ROLES)}")
        if role in self.models:
            return self.models[role]
        tier = self.tier_for(role)
        model = self.tier_model(tier)
        if model is not None:
            return model
        raise ConfigError(f"No model for {role} (tier {tier}). Set models.{tier} in ~/.phil/config.toml or phil.toml.")

    def missing_models(self, roles: tuple[str, ...]) -> list[str]:
        missing = []
        for role in roles:
            try:
                self.model_for(role)
            except ConfigError:
                missing.append(role)
        return missing

    def missing_keys(self, roles: tuple[str, ...], environ: Mapping[str, str]) -> list[str]:
        """API-key variables that the models resolved for `roles` need but `environ` lacks, in role order."""
        missing: list[str] = []
        for role in roles:
            try:
                model = self.model_for(role)
            except ConfigError:
                continue  # missing_models reports an unset model; this only checks keys for a set one
            provider = model.partition(":")[0]
            key = PROVIDER_KEYS.get(provider)
            if key and not environ.get(key) and key not in missing:
                missing.append(key)
        return missing

    def budget_for(self, role: str) -> RoleBudget:
        default = RoleBudget(max_input_tokens=DEFAULT_BUDGETS.get(role, 12_000))
        return self.budget.get(role, default)


DEFAULT_SOURCE = "default"
REPO_SOURCE = "phil.toml"
SET_SOURCE = "--set"


def global_config_path() -> Path:
    return phil_home() / "config.toml"


def parse_override(text: str) -> tuple[list[str], object]:
    """`key.path=value` from `--set`: the value is parsed as TOML, else kept as the raw string."""
    key, sep, value = text.partition("=")
    path = key.strip().split(".")
    if not sep or not key.strip() or any(not part.strip() for part in path):
        raise ConfigError(f"--set expects key.path=value: {text}")
    try:
        parsed: object = tomllib.loads("v = " + value)["v"]
    except tomllib.TOMLDecodeError:
        parsed = value
    return [part.strip() for part in path], parsed


def _leaves(data: Mapping, prefix: tuple[str, ...] = ()) -> list[str]:
    paths: list[str] = []
    for key, value in data.items():
        path = (*prefix, str(key))
        if isinstance(value, Mapping):
            paths.extend(_leaves(value, path))
        else:
            paths.append(".".join(path))
    return paths


def _merge(base: dict, layer: Mapping, source: str, sources: dict[str, str], prefix: tuple[str, ...] = ()) -> None:
    """Merge `layer` into `base` in place: dicts merge recursively, anything else replaces."""
    for key, value in layer.items():
        path = ".".join((*prefix, key))
        if isinstance(value, Mapping):
            sources.pop(path, None)  # a table replaces a lower layer's leaf here; tables merge
            if not isinstance(base.get(key), dict):
                base[key] = {}
            _merge(base[key], value, source, sources, (*prefix, key))
        else:
            # A leaf replaces whatever the lower layers set at or under this key.
            for stale in [p for p in sources if p.startswith(path + ".")]:
                del sources[stale]
            base[key] = value
            sources[path] = source


def _read_layer(path: Path, label: str) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid {label}: {exc}") from exc


def _precedence(source: str) -> int:
    return {DEFAULT_SOURCE: 0, REPO_SOURCE: 2, SET_SOURCE: 3}.get(source, 1)  # any other source is the global file


def _source_of(loc: tuple, sources: Mapping[str, str]) -> str:
    """The layer that set the value at pydantic's `loc`, or the key that introduced it."""
    parts = [str(part) for part in loc]
    while parts:
        path = ".".join(parts)
        if path in sources:
            return sources[path]
        under = [src for leaf, src in sources.items() if leaf.startswith(path + ".")]
        if under:
            # A table-level failure (e.g. an unknown role in [models]): the highest layer under it.
            return max(under, key=_precedence)
        parts.pop()
    return "config"


def load_config(repo_root: Path, *, overrides: Sequence[str] = ()) -> PhilConfig:
    """Settings in layers: defaults, then ~/.phil/config.toml, then the repo's phil.toml, then `--set`."""
    parsed_overrides = [parse_override(text) for text in overrides]
    merged: dict = {}
    sources: dict[str, str] = dict.fromkeys(_leaves(PhilConfig().model_dump()), DEFAULT_SOURCE)
    global_path = global_config_path()
    if global_path.is_file():
        _merge(merged, _read_layer(global_path, str(global_path)), str(global_path), sources)
    repo_path = repo_root / "phil.toml"
    if repo_path.is_file():
        _merge(merged, _read_layer(repo_path, REPO_SOURCE), REPO_SOURCE, sources)
    for path, value in parsed_overrides:
        layer: object = value
        for part in reversed(path):
            layer = {part: layer}
        _merge(merged, layer, SET_SOURCE, sources)  # type: ignore[arg-type]
    try:
        config = PhilConfig(**merged)
    except ValidationError as exc:
        error = exc.errors()[0]
        loc = ".".join(str(part) for part in error["loc"])
        raise ConfigError(f"Invalid {_source_of(error['loc'], sources)}: {loc}: {error['msg']}") from exc
    config._sources = sources
    return config


def effective_toml(config: PhilConfig) -> str:
    """The merged settings as TOML, each leaf line ending `# from <source>`."""
    data = config.model_dump()
    comments = {leaf: f"from {config.sources.get(leaf, DEFAULT_SOURCE)}" for leaf in _leaves(data)}
    return dump_toml(data, comments)
