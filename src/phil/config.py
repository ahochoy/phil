import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

ROLES = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")
DEFAULT_MODEL = "openrouter:poolside/laguna-m.1:free"


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
    max_tokens: int = 400_000
    max_cost_usd: float = 2.0


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
    ]


class PhilConfig(_Section):
    models: dict[str, str] = Field(default_factory=lambda: {role: DEFAULT_MODEL for role in ROLES})
    budget: dict[str, RoleBudget] = {}
    run: RunConfig = RunConfig()
    shell: ShellConfig = ShellConfig()
    project: ProjectConfig = ProjectConfig()

    @model_validator(mode="before")
    @classmethod
    def _fill_default_models(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        data["models"] = {role: DEFAULT_MODEL for role in ROLES} | (data.get("models") or {})
        return data

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
        return self.models[role]

    def budget_for(self, role: str) -> RoleBudget:
        return self.budget.get(role, RoleBudget())


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
