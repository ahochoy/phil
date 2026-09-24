from pathlib import Path

import pytest

from tests.helpers import run_git


@pytest.fixture(autouse=True)
def phil_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "phil_home"
    monkeypatch.setenv("PHIL_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests must not pick up the machine's own global/system git config (e.g. a real
    # gpg.format=ssh signing key), or they could sign test commits with the user's real key.
    global_config = tmp_path / "gitconfig"
    global_config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


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
