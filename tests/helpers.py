import subprocess
from pathlib import Path

from phil.config import ROLES

TEST_MODEL = "test:model"
TEST_MODELS = {role: TEST_MODEL for role in ROLES}

MODELS_TOML = "[models]\n" + "".join(f'{role} = "{TEST_MODEL}"\n' for role in ROLES)


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
