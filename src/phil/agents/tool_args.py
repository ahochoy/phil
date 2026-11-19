"""Tool-call arguments some models get wrong in a predictable way, put right before validation.

Live, Ollama's flint called read_file and write_file with `path` (the name other harnesses use)
instead of `file_path`, and spent a whole run's model calls on the validation errors.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)

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


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _mended(raw: str) -> str:
    """`raw` with trailing commas dropped, an open string closed, and open brackets closed."""
    out: list[str] = []
    stack: list[str] = []
    in_string = escaped = False
    pending_comma = False
    for char in raw:
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char.isspace():
            out.append(char)
            continue
        if char == ",":
            if pending_comma:
                continue
            pending_comma = True
            out.append(char)
            continue
        if char in "}]" and pending_comma:
            comma = max(i for i, c in enumerate(out) if c == ",")  # the last comma written
            del out[comma]
        pending_comma = False
        out.append(char)
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]" and stack:
            stack.pop()
    if in_string:
        if escaped:
            out.pop()  # a lone trailing backslash escapes nothing
        out.append('"')
    elif pending_comma:
        del out[max(i for i, c in enumerate(out) if c == ",")]
    out.extend(reversed(stack))
    return "".join(out)


def repair_arguments(raw: str) -> dict[str, Any]:
    """The JSON object a model meant by `raw`: trailing commas dropped, an open string and open
    brackets closed. `{}` when that still isn't an object (the call and its result must both stay)."""
    parsed = _loads_object(raw)
    if parsed is not None:
        return parsed
    parsed = _loads_object(_mended(raw))
    if parsed is None:
        logger.debug("tool call arguments not repairable, sending {}: %r", raw[:200])
        return {}
    logger.debug("repaired tool call arguments: %r", raw[:200])
    return parsed


def _repaired_message(message: Any) -> Any | None:
    """A copy of an AI `message` with malformed tool-call arguments put right, or None if there are none."""
    update: dict[str, Any] = {}
    if message.invalid_tool_calls:
        update["tool_calls"] = [
            *message.tool_calls,
            *(
                {"id": call.get("id"), "name": call.get("name") or "", "args": repair_arguments(call.get("args") or ""), "type": "tool_call"}
                for call in message.invalid_tool_calls
            ),
        ]
        update["invalid_tool_calls"] = []
    raw_calls = message.additional_kwargs.get("tool_calls")
    if isinstance(raw_calls, list):
        fixed_calls = []
        changed = False
        for raw in raw_calls:
            function = raw.get("function") if isinstance(raw, dict) else None
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if isinstance(arguments, str) and _loads_object(arguments) is None:
                raw = {**raw, "function": {**function, "arguments": json.dumps(repair_arguments(arguments))}}
                changed = True
            fixed_calls.append(raw)
        if changed:
            update["additional_kwargs"] = {**message.additional_kwargs, "tool_calls": fixed_calls}
    return message.model_copy(update=update) if update else None


def repaired_history(messages: list[Any]) -> list[Any] | None:
    """`messages` with every AI message's malformed tool calls repaired, or None when none needed it.
    A provider rejects the whole request over one call whose arguments aren't valid JSON. The
    originals are never changed."""
    result = []
    changed = False
    for message in messages:
        fixed = _repaired_message(message) if getattr(message, "type", None) == "ai" else None
        changed = changed or fixed is not None
        result.append(message if fixed is None else fixed)
    return result if changed else None


class ToolCallRepair(AgentMiddleware):
    """Repairs malformed tool calls in the history before each model call (outgoing request only)."""

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        messages = repaired_history(request.messages)
        return handler(request if messages is None else request.override(messages=messages))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        messages = repaired_history(request.messages)
        return await handler(request if messages is None else request.override(messages=messages))


def tool_args_middleware() -> Any:
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
