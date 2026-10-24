"""Tool-call arguments some models get wrong in a predictable way, put right before validation.

Live, Ollama's flint called read_file and write_file with `path` (the name other harnesses use)
instead of `file_path`, and spent a whole run's model calls on the validation errors.
"""

from typing import Any

_ALIASES = {"path": "file_path"}  # what a model sent → the name the tool takes


def aliased_args(args: dict[str, Any], params: set[str]) -> dict[str, Any] | None:
    """`args` with each alias renamed to the parameter it stands for, or None when nothing changes.
    Only for a tool that takes that parameter and not the alias itself, and never over a value the
    model also sent under the right name."""
    renamed = {
        alias: name
        for alias, name in _ALIASES.items()
        if alias in args and name not in args and name in params and alias not in params
    }
    if not renamed:
        return None
    return {renamed.get(key, key): value for key, value in args.items()}


def tool_args_middleware() -> Any:
    from langchain.agents.middleware import AgentMiddleware

    def fixed(request: Any) -> Any:
        tool = request.tool
        call = request.tool_call
        args = call.get("args")
        if tool is None or not isinstance(args, dict):
            return request
        new = aliased_args(args, set(tool.args))
        return request if new is None else request.override(tool_call={**call, "args": new})

    class ToolArgs(AgentMiddleware):
        def wrap_tool_call(self, request: Any, handler: Any) -> Any:
            return handler(fixed(request))

        async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
            return await handler(fixed(request))

    return ToolArgs()
