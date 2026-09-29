from collections.abc import Callable
from pathlib import Path
from typing import Any

from phil.agents.spec import AgentSpec, load_prompt


def filesystem_permissions(spec: AgentSpec) -> list[Any]:
    from deepagents import FilesystemPermission

    rules = [FilesystemPermission(operations=["write"], paths=["/.git", "/.git/**", "/phil.toml"], mode="deny")]
    if not spec.writes_files:
        rules.append(
            FilesystemPermission(operations=["write"], paths=["/**", "/**/.*", "/**/.*/**"], mode="deny")
        )
    return rules


def _tool_strategy(spec: AgentSpec) -> Any:
    # Always return the contract through a tool call. LangChain otherwise auto-selects the provider's
    # native JSON-schema mode for some model names (e.g. gpt-*), which some providers reject; tool
    # calling is the most widely supported path across providers.
    from langchain.agents.structured_output import ToolStrategy

    return ToolStrategy(spec.out_contract)


# Per-provider kwargs for `init_chat_model` that put a ceiling on every model call and turn off
# the provider SDK's own retries (phil.agents.retry.call_with_retry does the retrying instead,
# so it alone controls backoff and counts attempts). Units and the kwarg name that reaches the
# SDK differ by provider:
#  - openrouter: `ChatOpenRouter.request_timeout` (alias `timeout`) is milliseconds, mapped to
#    the SDK's `timeout_ms`.
#  - openai/anthropic/google_genai: `timeout` is seconds.
# An unknown/unlisted provider (a local server, a custom endpoint, or a bare model name in
# tests) gets no extra kwargs.
_PROVIDER_TIMEOUT_KWARGS: dict[str, Callable[[int], dict[str, Any]]] = {
    "openrouter": lambda timeout_s: {"timeout": timeout_s * 1000, "max_retries": 0},
    "openai": lambda timeout_s: {"timeout": timeout_s, "max_retries": 0},
    "anthropic": lambda timeout_s: {"timeout": timeout_s, "max_retries": 0},
    "google_genai": lambda timeout_s: {"timeout": timeout_s, "max_retries": 0},
}


def chat_model(model: str, timeout_s: int) -> Any:
    """Build the `BaseChatModel` for `model`, capping every call at `timeout_s` and disabling
    the provider SDK's own retries."""
    from langchain.chat_models import init_chat_model

    provider = model.partition(":")[0]
    kwargs_for = _PROVIDER_TIMEOUT_KWARGS.get(provider)
    return init_chat_model(model, **(kwargs_for(timeout_s) if kwargs_for else {}))


def build_agent(
    spec: AgentSpec,
    model: str,
    workdir: Path | None,
    tools: list[Callable[..., str]],
    *,
    timeout_s: int = 180,
) -> Any:
    if spec.harness == "lean":
        return _build_lean_agent(spec, model, tools, timeout_s)
    return _build_deep_agent(spec, model, workdir, tools, timeout_s)


def _build_lean_agent(spec: AgentSpec, model: str, tools: list[Callable[..., str]], timeout_s: int) -> Any:
    if spec.tools or tools:
        raise ValueError(f"{spec.name} uses the lean harness, which does not support tools")
    from langchain.agents import create_agent

    return create_agent(
        chat_model(model, timeout_s),
        tools=[],
        system_prompt=load_prompt(spec),
        response_format=_tool_strategy(spec),
    )


def _build_deep_agent(
    spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]], timeout_s: int
) -> Any:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=workdir, virtual_mode=True) if workdir is not None else None
    return create_deep_agent(
        model=chat_model(model, timeout_s),
        tools=tools,
        system_prompt=load_prompt(spec),
        backend=backend,
        permissions=filesystem_permissions(spec),
        response_format=_tool_strategy(spec),
    )
