import subprocess
import sys

import pytest
from deepagents.middleware.filesystem import _check_fs_permission

from phil.agents.factory import build_deep_agent, filesystem_permissions
from phil.agents.registry import get_spec


def test_importing_invoke_does_not_load_llm_stack():
    code = (
        "import sys, phil.agents.invoke, phil.packets; "
        "heavy = ('deepagents', 'langchain', 'langchain_core', 'langgraph', 'langchain_openrouter'); "
        "print(','.join(m for m in heavy if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


READ_ONLY_ROLES = ["reviewer", "architect", "critic"]
WRITER_ROLES = ["implementer", "tester"]

DOTFILE_AND_PROJECT_PATHS = [
    "/src/app.py",
    "/app.py",
    "/.env",
    "/.gitignore",
    "/.github/workflows/ci.yml",
    "/src/.hidden",
    "/.git",
    "/.git/config",
    "/phil.toml",
]

ALWAYS_DENIED_PATHS = ["/.git", "/.git/config", "/phil.toml"]
WRITER_ALLOWED_PATHS = ["/src/app.py", "/tests/test_app.py", "/.gitignore", "/src/.hidden"]


@pytest.mark.parametrize("name", READ_ONLY_ROLES)
@pytest.mark.parametrize("path", DOTFILE_AND_PROJECT_PATHS)
def test_read_only_roles_deny_write_everywhere_including_dotfiles(name, path):
    rules = filesystem_permissions(get_spec(name))
    assert _check_fs_permission(rules, "write", path) == "deny"


@pytest.mark.parametrize("name", WRITER_ROLES)
@pytest.mark.parametrize("path", ALWAYS_DENIED_PATHS)
def test_writer_roles_still_deny_git_and_config(name, path):
    rules = filesystem_permissions(get_spec(name))
    assert _check_fs_permission(rules, "write", path) == "deny"


@pytest.mark.parametrize("name", WRITER_ROLES)
@pytest.mark.parametrize("path", WRITER_ALLOWED_PATHS)
def test_writer_roles_allow_project_and_dotfile_writes(name, path):
    rules = filesystem_permissions(get_spec(name))
    assert _check_fs_permission(rules, "write", path) == "allow"


def test_build_returns_invokable_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    agent = build_deep_agent(get_spec("critic"), "openrouter:poolside/laguna-m.1:free", tmp_path, [])
    assert hasattr(agent, "invoke")
