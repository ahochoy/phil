import subprocess
import sys

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


def rules(name):
    return [(rule.operations, rule.paths, rule.mode) for rule in filesystem_permissions(get_spec(name))]


def test_read_only_roles_cannot_write():
    assert (["write"], ["/**"], "deny") in rules("reviewer")
    assert (["write"], ["/**"], "deny") in rules("architect")


def test_writers_are_kept_out_of_git_and_config():
    implementer = rules("implementer")
    assert (["write"], ["/.git/**", "/phil.toml"], "deny") in implementer
    assert (["write"], ["/**"], "deny") not in implementer


def test_build_returns_invokable_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    agent = build_deep_agent(get_spec("critic"), "openrouter:poolside/laguna-m.1:free", tmp_path, [])
    assert hasattr(agent, "invoke")
