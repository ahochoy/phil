import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROLES = ("orchestrator", "architect", "critic", "implementer", "tester", "reviewer")
DEFAULT_MODEL = "openrouter:poolside/laguna-m.1:free"


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

    def model_for(self, role: str) -> str:
        return self.models[role]

    def budget_for(self, role: str) -> RoleBudget:
        return self.budget.get(role, RoleBudget())


def load_config(repo_root: Path) -> PhilConfig:
    path = repo_root / "phil.toml"
    if not path.exists():
        return PhilConfig()
    data = tomllib.loads(path.read_text())
    models = {role: DEFAULT_MODEL for role in ROLES} | data.pop("models", {})
    return PhilConfig(models=models, **data)
