import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

ROLES = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")
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
    # No default model: each role's model is chosen explicitly in phil.toml.
    models: dict[str, str] = {}
    budget: dict[str, RoleBudget] = {}
    run: RunConfig = RunConfig()
    shell: ShellConfig = ShellConfig()
    project: ProjectConfig = ProjectConfig()
    git: GitConfig = GitConfig()

    @field_validator("models")
    @classmethod
    def _validate_model_roles(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - set(ROLES))
        if unknown:
            raise ValueError(f"Unknown role(s) in [models]: {unknown}. Valid roles: {list(ROLES)}")
        return value

    @field_validator("budget")
    @classmethod
    def _validate_budget_roles(cls, value: dict[str, RoleBudget]) -> dict[str, RoleBudget]:
        unknown = sorted(set(value) - set(ROLES))
        if unknown:
            raise ValueError(f"Unknown role(s) in [budget]: {unknown}. Valid roles: {list(ROLES)}")
        return value

    def model_for(self, role: str) -> str:
        if role not in ROLES:
            raise ConfigError(f"Unknown role {role!r}. Valid roles: {list(ROLES)}")
        if role not in self.models:
            raise ConfigError(f'No model set for {role}. Add {role} = "provider:model" under [models] in phil.toml.')
        return self.models[role]

    def missing_models(self, roles: tuple[str, ...]) -> list[str]:
        return [role for role in roles if role not in self.models]

    def missing_keys(self, roles: tuple[str, ...], environ: Mapping[str, str]) -> list[str]:
        """API-key variables that the models set for `roles` need but `environ` lacks, in role order."""
        missing: list[str] = []
        for role in roles:
            provider = self.models.get(role, "").partition(":")[0]
            key = PROVIDER_KEYS.get(provider)
            if key and not environ.get(key) and key not in missing:
                missing.append(key)
        return missing

    def budget_for(self, role: str) -> RoleBudget:
        default = RoleBudget(max_input_tokens=DEFAULT_BUDGETS.get(role, 12_000))
        return self.budget.get(role, default)


def load_config(repo_root: Path) -> PhilConfig:
    path = repo_root / "phil.toml"
    if not path.exists():
        return PhilConfig()
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid phil.toml: {exc}") from exc
    try:
        return PhilConfig(**data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid phil.toml: {exc}") from exc
