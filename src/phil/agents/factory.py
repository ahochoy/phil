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


def build_agent(spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]) -> Any:
    if spec.harness == "lean":
        return _build_lean_agent(spec, model, tools)
    return _build_deep_agent(spec, model, workdir, tools)


def _build_lean_agent(spec: AgentSpec, model: str, tools: list[Callable[..., str]]) -> Any:
    if spec.tools or tools:
        raise ValueError(f"{spec.name} uses the lean harness, which does not support tools")
    from langchain.agents import create_agent

    return create_agent(model, tools=[], system_prompt=load_prompt(spec), response_format=_tool_strategy(spec))


def _build_deep_agent(spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]) -> Any:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=workdir, virtual_mode=True) if workdir is not None else None
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=load_prompt(spec),
        backend=backend,
        permissions=filesystem_permissions(spec),
        response_format=_tool_strategy(spec),
    )
