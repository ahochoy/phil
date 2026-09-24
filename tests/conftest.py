from pathlib import Path

import pytest

from tests.helpers import run_git


@pytest.fixture(autouse=True)
def phil_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "phil_home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    return home


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    repo = repo.resolve()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "init")
    return repo
