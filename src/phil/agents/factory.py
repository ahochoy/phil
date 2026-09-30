from collections.abc import Callable
from pathlib import Path
from typing import Any

from phil.agents.providers import ProviderSpec, build_chat_model, resolve_provider, split_model
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


def chat_model(
    model: str, timeout_s: int, provider: ProviderSpec | None = None, used_by: tuple[str, ...] = ()
) -> Any:
    """Build the `BaseChatModel` for `model` (`provider:name`) on `provider` (resolved from the
    built-ins when not given), capping every call at `timeout_s` and disabling the provider SDK's
    own retries: phil.agents.model_retry retries each failed model call instead, so Phil alone
    controls backoff and counts attempts. `used_by` names the roles in a missing-key error."""
    from phil.config import PhilConfig

    name, model_name = split_model(model)
    spec = provider if provider is not None else resolve_provider(PhilConfig(), name)
    return build_chat_model(spec, model_name, timeout_s, used_by=used_by)


def build_agent(
    spec: AgentSpec,
    model: str,
    workdir: Path | None,
    tools: list[Callable[..., str]],
    *,
    timeout_s: int = 180,
    provider: ProviderSpec | None = None,
) -> Any:
    """`provider` is the resolved provider for `model` (invoke_agent resolves it from the config);
    without one, `model`'s provider must be a built-in."""
    if spec.harness == "lean":
        return _build_lean_agent(spec, model, tools, timeout_s, provider)
    return _build_deep_agent(spec, model, workdir, tools, timeout_s, provider)


def _build_lean_agent(
    spec: AgentSpec, model: str, tools: list[Callable[..., str]], timeout_s: int, provider: ProviderSpec | None
) -> Any:
    if spec.tools or tools:
        raise ValueError(f"{spec.name} uses the lean harness, which does not support tools")
    from langchain.agents import create_agent

    from phil.agents.model_retry import PhilModelRetryMiddleware

    middleware: list[Any] = [PhilModelRetryMiddleware()]
    if spec.end_on_text:
        middleware.append(_end_on_text_middleware())
    return create_agent(
        chat_model(model, timeout_s, provider=provider, used_by=(spec.role,)),
        tools=[],
        system_prompt=load_prompt(spec),
        response_format=_tool_strategy(spec),
        middleware=middleware,
    )


def _end_on_text_middleware() -> Any:
    """Ends a lean agent's loop after a model answer with no tool call, leaving no structured
    output (invoke_agent then records the answer's text as the rejected output). Only a plain AI
    answer ends it: an invalid structured-output call leaves a ToolMessage with the error last, and
    LangChain's own loop asks the model to fix its arguments."""
    from langchain.agents.middleware import AgentMiddleware, hook_config

    class EndOnText(AgentMiddleware):
        @hook_config(can_jump_to=["end"])
        def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            last = state["messages"][-1] if state["messages"] else None
            is_text_answer = getattr(last, "type", None) == "ai" and not getattr(last, "tool_calls", None)
            if state.get("structured_response") is None and is_text_answer:
                return {"jump_to": "end"}
            return None

    return EndOnText()


def _build_deep_agent(
    spec: AgentSpec,
    model: str,
    workdir: Path | None,
    tools: list[Callable[..., str]],
    timeout_s: int,
    provider: ProviderSpec | None,
) -> Any:
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    from phil.agents.model_retry import PhilModelRetryMiddleware

    backend = FilesystemBackend(root_dir=workdir, virtual_mode=True) if workdir is not None else None
    return create_deep_agent(
        model=chat_model(model, timeout_s, provider=provider, used_by=(spec.role,)),
        tools=tools,
        system_prompt=load_prompt(spec),
        backend=backend,
        permissions=filesystem_permissions(spec),
        response_format=_tool_strategy(spec),
        middleware=[PhilModelRetryMiddleware()],
        subagents=[_general_purpose_subagent()],
    )


def _general_purpose_subagent() -> Any:
    """deepagents' default general-purpose sub-agent, spelled out so it gets Phil's per-call
    retry middleware: `create_deep_agent(middleware=...)` applies to the main agent only — the
    auto-added general-purpose sub-agent inherits just the entries that replace one of its own
    default middleware by name. Passing a spec named "general-purpose" replaces the default;
    as a declarative spec it still gets deepagents' base stack (filesystem with the parent's
    permissions, summarization, patch-tool-calls, prompt caching), the parent's model and
    tools, and the default description and prompt."""
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from phil.agents.model_retry import PhilModelRetryMiddleware

    return {**GENERAL_PURPOSE_SUBAGENT, "middleware": [PhilModelRetryMiddleware()]}
