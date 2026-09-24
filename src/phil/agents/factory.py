from collections.abc import Callable
from pathlib import Path
from typing import Any

from phil.agents.spec import AgentSpec, load_prompt


def filesystem_permissions(spec: AgentSpec) -> list[Any]:
    from deepagents import FilesystemPermission

    rules = [FilesystemPermission(operations=["write"], paths=["/.git/**", "/phil.toml"], mode="deny")]
    if not spec.writes_files:
        rules.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"))
    return rules


def build_deep_agent(
    spec: AgentSpec, model: str, workdir: Path | None, tools: list[Callable[..., str]]
) -> Any:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=workdir, virtual_mode=True) if workdir is not None else None
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=load_prompt(spec),
        backend=backend,
        permissions=filesystem_permissions(spec),
        response_format=spec.out_contract,
    )
