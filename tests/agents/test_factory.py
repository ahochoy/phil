import subprocess
import sys
from dataclasses import replace

import pytest
from deepagents.middleware.filesystem import _check_fs_permission

from phil.agents.factory import build_agent, chat_model, filesystem_permissions
from phil.agents.providers import BUILTIN_PROVIDERS, ProviderSpec
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
    sentinel = object()

    def fake_create_agent(model, tools=None, **kwargs):
        captured.update(model=model, tools=tools, **kwargs)
        return "lean-agent"

    def forbidden(*args, **kwargs):
        raise AssertionError("lean roles must not use deepagents")

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    monkeypatch.setattr(deepagents, "create_deep_agent", forbidden)
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda model, timeout_s, provider=None: sentinel)
    spec = get_spec("critic")
    assert build_agent(spec, "m", tmp_path, []) == "lean-agent"
    assert captured["model"] is sentinel
    assert captured["tools"] == []
    assert captured["response_format"].schema is spec.out_contract
    assert captured["system_prompt"] == load_prompt(spec)
    assert [type(m).__name__ for m in captured["middleware"]] == ["PhilModelRetryMiddleware"]


def test_deep_roles_use_deepagents_with_permissions(tmp_path, monkeypatch):
    import deepagents

    captured = {}
    sentinel = object()

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return "deep-agent"

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr("phil.agents.factory.chat_model", lambda model, timeout_s, provider=None: sentinel)
    spec = get_spec("architect")
    assert build_agent(spec, "m", tmp_path, []) == "deep-agent"
    assert captured["model"] is sentinel
    assert captured["permissions"]
    assert captured["response_format"].schema is spec.out_contract
    assert [type(m).__name__ for m in captured["middleware"]] == ["PhilModelRetryMiddleware"]
    # the general-purpose sub-agent doesn't inherit the parent's middleware: it is passed
    # explicitly, with deepagents' default description and prompt, plus the retry middleware
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    [general] = captured["subagents"]
    assert {k: general[k] for k in GENERAL_PURPOSE_SUBAGENT} == GENERAL_PURPOSE_SUBAGENT
    assert [type(m).__name__ for m in general["middleware"]] == ["PhilModelRetryMiddleware"]


def test_lean_harness_rejects_tools(tmp_path):
    spec = replace(get_spec("critic"), tools=("shell",))
    with pytest.raises(ValueError, match="lean"):
        build_agent(spec, "m", tmp_path, [])


@pytest.mark.parametrize("name", ["critic", "architect"])
def test_agents_use_tool_calling_for_structured_output(name, tmp_path, monkeypatch):
    # Provider-native JSON-schema output (ProviderStrategy) is auto-selected for some model names
    # and is rejected by some providers; a tool call works across providers.
    from langchain.agents.structured_output import ToolStrategy

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("langchain.agents.create_agent", fake)
    monkeypatch.setattr("deepagents.create_deep_agent", fake)
    spec = get_spec(name)
    build_agent(spec, "openrouter:openai/gpt-6-luna", tmp_path, [])
    assert isinstance(captured["response_format"], ToolStrategy)
    assert captured["response_format"].schema is spec.out_contract


def test_build_agent_passes_timeout_s_and_provider_to_chat_model(tmp_path, monkeypatch):
    import langchain.agents

    seen: list[tuple] = []

    monkeypatch.setattr(langchain.agents, "create_agent", lambda *a, **kw: "lean-agent")
    monkeypatch.setattr(
        "phil.agents.factory.chat_model",
        lambda model, timeout_s, provider=None: (seen.append((timeout_s, provider)), object())[1],
    )
    spec = get_spec("critic")
    lab = ProviderSpec("lab", "openai", "http://lab:8000/v1", None, None, None)
    build_agent(spec, "lab:m", tmp_path, [], timeout_s=42, provider=lab)
    build_agent(spec, "m", tmp_path, [])
    assert seen == [(42, lab), (180, None)]


# --- model construction goes through phil.agents.providers ---------------------------------


def test_chat_model_builds_through_the_given_provider(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "phil.agents.factory.build_chat_model",
        lambda spec, name, timeout_s: (captured.append((spec, name, timeout_s)), "model-object")[1],
    )
    lab = ProviderSpec("lab", "openai", "http://lab:8000/v1", None, None, None)
    assert chat_model("lab:qwen3:32b", 42, provider=lab) == "model-object"
    assert captured == [(lab, "qwen3:32b", 42)]


def test_chat_model_without_a_provider_resolves_a_builtin(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "phil.agents.factory.build_chat_model",
        lambda spec, name, timeout_s: (captured.append((spec, name, timeout_s)), "model-object")[1],
    )
    chat_model("google_genai:gemini-2.5-flash", 90)
    assert captured == [(BUILTIN_PROVIDERS["google"], "gemini-2.5-flash", 90)]


def test_chat_model_openrouter_uses_millisecond_timeout_and_disables_sdk_retries(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    model = chat_model("openrouter:openai/gpt-6-luna", 180)
    assert model.request_timeout == 180_000
    assert model.max_retries == 0
