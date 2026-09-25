import subprocess
import sys
from dataclasses import replace

import pytest
from deepagents.middleware.filesystem import _check_fs_permission

from phil.agents.factory import build_agent, filesystem_permissions
from phil.agents.registry import get_spec
from phil.agents.spec import load_prompt


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


@pytest.mark.parametrize("name", ["critic", "architect"])
def test_build_returns_invokable_agent(name, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    agent = build_agent(get_spec(name), "openrouter:openai/gpt-6-luna", tmp_path, [])
    assert hasattr(agent, "invoke")


def test_lean_roles_use_plain_create_agent(tmp_path, monkeypatch):
    import deepagents
    import langchain.agents

    captured = {}

    def fake_create_agent(model, tools=None, **kwargs):
        captured.update(model=model, tools=tools, **kwargs)
        return "lean-agent"

    def forbidden(*args, **kwargs):
        raise AssertionError("lean roles must not use deepagents")

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    monkeypatch.setattr(deepagents, "create_deep_agent", forbidden)
    spec = get_spec("critic")
    assert build_agent(spec, "m", tmp_path, []) == "lean-agent"
    assert captured["model"] == "m"
    assert captured["tools"] == []
    assert captured["response_format"] is spec.out_contract
    assert captured["system_prompt"] == load_prompt(spec)


def test_deep_roles_use_deepagents_with_permissions(tmp_path, monkeypatch):
    import deepagents

    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return "deep-agent"

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    spec = get_spec("architect")
    assert build_agent(spec, "m", tmp_path, []) == "deep-agent"
    assert captured["permissions"]
    assert captured["response_format"] is spec.out_contract


def test_lean_harness_rejects_tools(tmp_path):
    spec = replace(get_spec("critic"), tools=("shell",))
    with pytest.raises(ValueError, match="lean"):
        build_agent(spec, "m", tmp_path, [])
