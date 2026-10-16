import os
import shutil
import subprocess
from pathlib import Path

from phil.config import ROLES

TEST_MODEL = "ollama:test-model"
TEST_MODELS = {role: TEST_MODEL for role in ROLES}

MODELS_TOML = "[models]\n" + "".join(f'{role} = "{TEST_MODEL}"\n' for role in ROLES)


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
    ).stdout


def build_template_repo(repo: Path, files: dict[str, str]) -> Path:
    """A committed repo on `main` holding `files`, for a session-scoped fixture to copy into each test
    (copying is much cheaper than running git six times per test, above all on Windows).

    Session fixtures run before the per-test `isolated_git_config`, so this isolates git itself: the
    machine's own config (a signing key, a hooks path, init templates) must not reach the template."""
    repo.mkdir(parents=True)
    empty = repo.parent / f"{repo.name}.gitconfig"
    empty.write_text("")
    env = os.environ | {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
    run_git(repo, "init", "-b", "main", env=env)
    run_git(repo, "config", "user.email", "test@example.com", env=env)
    run_git(repo, "config", "user.name", "Test", env=env)
    run_git(repo, "config", "commit.gpgsign", "false", env=env)
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    run_git(repo, "add", "-A", env=env)
    run_git(repo, "commit", "-m", "init", env=env)
    return repo


def copy_repo(template: Path, dest: Path) -> Path:
    """A fresh copy of a template repo at `dest`, resolved (as the fixtures always returned it)."""
    shutil.copytree(template, dest, symlinks=True)
    return dest.resolve()
