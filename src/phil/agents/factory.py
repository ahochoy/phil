import itertools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from phil.agents.providers import ProviderSpec, build_chat_model, resolve_provider, split_model
from phil.agents.spec import ANSWER_NOW, AgentSpec, load_prompt


def _case_variants(name: str) -> list[str]:
    """Every upper/lower-case spelling of `name` (".git" gives 8)."""
    return ["".join(chars) for chars in itertools.product(*({c.lower(), c.upper()} for c in name))]


def _protected_paths() -> list[str]:
    """/.git (and everything under it) and /phil.toml, in every case spelling.

    deepagents matches permission globs case-sensitively, but macOS's default filesystem is
    case-insensitive, so "/PHIL.toml" would otherwise reach phil.toml. The variants are literal
    paths, not bracket globs like "/[pP]hil.toml": deepagents anchors a recursive delete's check
    at a pattern's wildcard-free prefix, and a bracket in the first segment anchors it at "/",
    which would refuse every directory delete."""
    git = [p for variant in _case_variants(".git") for p in (f"/{variant}", f"/{variant}/**")]
    return git + [f"/{variant}" for variant in _case_variants("phil.toml")]


def filesystem_permissions(spec: AgentSpec) -> list[Any]:
    from deepagents import FilesystemPermission

    rules = [FilesystemPermission(operations=["write"], paths=_protected_paths(), mode="deny")]
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
    if spec.harness == "light":
        return _build_light_agent(spec, model, workdir, tools, timeout_s, provider)
    return _build_deep_agent(spec, model, workdir, tools, timeout_s, provider)


READ_TOOLS = ("ls", "read_file", "glob", "grep")
WRITE_TOOLS = ("write_file", "edit_file", "delete")


BUDGET_GRACE_CALLS = 2  # capped calls allowed to fix an invalid answer before the hard stop


def _ai_calls(messages: list[Any]) -> int:
    return sum(1 for m in messages if getattr(m, "type", None) == "ai")


def _with_cap_message(system_message: Any, message: str) -> Any:
    """`system_message` (or none) with `message` appended. Plain-string content stays a plain
    string (OpenAI-compatible servers can reject list content); block content keeps its blocks."""
    from langchain_core.messages import SystemMessage

    content = system_message.content if system_message is not None else ""
    if isinstance(content, str):
        return SystemMessage(f"{content}\n\n{message}" if content else message)
    blocks = list(system_message.content_blocks)
    blocks.append({"type": "text", "text": f"\n\n{message}" if blocks else message})
    return SystemMessage(content_blocks=blocks)


def _call_budget_middleware(max_calls: int, message: str = ANSWER_NOW) -> Any:
    """Cap an agent's model calls at `max_calls`, softly and then hard.

    Soft: from call `max_calls` on, remove the tools and add `message` (the spec's cap_message)
    telling the model to answer, so a capped agent still returns its structured output. (ToolStrategy adds its structured-output tool after
    this middleware runs, so `tools=[]` leaves the model exactly one tool: the answer.)

    Hard: an invalid answer is sent back to the model (ToolStrategy's handle_errors), and so is a
    plain-text one, so the soft cap alone could loop until LangGraph's recursion limit. Once
    `max_calls + BUDGET_GRACE_CALLS` calls are made, the loop ends without another model call and
    with no structured response; invoke_agent then records the rejected output and makes its
    single contract retry. One invoke_agent is thus bounded at
    `max_attempts * (max_calls + BUDGET_GRACE_CALLS)` model calls (2 * 14 = 28 for the answerer),
    not counting per-call transient retries."""
    from langchain.agents.middleware import AgentMiddleware, hook_config

    hard_stop = max_calls + BUDGET_GRACE_CALLS

    def capped(request: Any) -> Any:
        if _ai_calls(request.state["messages"]) >= max_calls - 1:
            return request.override(tools=[], system_message=_with_cap_message(request.system_message, message))
        return request

    def stop(state: Any) -> dict[str, Any] | None:
        if state.get("structured_response") is None and _ai_calls(state["messages"]) >= hard_stop:
            return {"jump_to": "end"}
        return None

    class CallBudget(AgentMiddleware):
        @hook_config(can_jump_to=["end"])
        def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return stop(state)

        @hook_config(can_jump_to=["end"])
        async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return stop(state)

        def wrap_model_call(self, request: Any, handler: Any) -> Any:
            return handler(capped(request))

        async def awrap_model_call(self, request: Any, handler: Any) -> Any:
            return await handler(capped(request))

    return CallBudget()


def _build_light_agent(
    spec: AgentSpec,
    model: str,
    workdir: Path | None,
    tools: list[Callable[..., str]],
    timeout_s: int,
    provider: ProviderSpec | None,
) -> Any:
    if workdir is None:
        raise ValueError(f"{spec.name} uses the light harness, which needs a workdir")
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents import create_agent

    from phil.agents.model_retry import PhilModelRetryMiddleware

    filesystem = FilesystemMiddleware(
        backend=FilesystemBackend(root_dir=workdir, virtual_mode=True),
        # A writing spec also gets the write tools (never `execute`: commands go through Phil's
        # shell tool); they check the permissions below, which deny .git and phil.toml.
        tools=[*READ_TOOLS, *WRITE_TOOLS] if spec.writes_files else list(READ_TOOLS),
        # deepagents offloads oversized tool results and user messages to the backend, which here
        # is the repository itself, and those writes skip the permission rules: keep it off.
        tool_token_limit_before_evict=None,
        human_message_token_limit_before_evict=None,
        _permissions=filesystem_permissions(spec),
    )
    middleware: list[Any] = [filesystem, PhilModelRetryMiddleware()]
    if spec.max_model_calls is not None:
        middleware.append(_call_budget_middleware(spec.max_model_calls, spec.cap_message))
    return create_agent(
        chat_model(model, timeout_s, provider=provider, used_by=(spec.role,)),
        tools=tools,
        system_prompt=load_prompt(spec),
        response_format=_tool_strategy(spec),
        middleware=middleware,
    )


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
    middleware: list[Any] = [PhilModelRetryMiddleware()]
    if spec.max_model_calls is not None:
        # Caps the main agent's calls; a sub-agent it starts runs on its own (uncapped) loop.
        middleware.append(_call_budget_middleware(spec.max_model_calls, spec.cap_message))
    return create_deep_agent(
        model=chat_model(model, timeout_s, provider=provider, used_by=(spec.role,)),
        tools=tools,
        system_prompt=load_prompt(spec),
        backend=backend,
        permissions=filesystem_permissions(spec),
        response_format=_tool_strategy(spec),
        middleware=middleware,
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
