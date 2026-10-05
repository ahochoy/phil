import ast
import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


@dataclass(frozen=True)
class ModelCall:
    model: str | None  # model name from the response/serialized llm, if known
    input_tokens: int
    output_tokens: int
    reported_cost: float | None
    cache_read_tokens: int = 0  # of input_tokens, how many were read from the provider's prompt cache


def _cache_reads(message: object) -> int:
    details = (getattr(message, "usage_metadata", None) or {}).get("input_token_details") or {}
    return int(details.get("cache_read", 0) or 0)


def _usage_from_message(message: object) -> tuple[int, int] | None:
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return None
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def _token_usage(usage: object) -> tuple[int, int] | None:
    """(input, output) tokens from an ``llm_output["token_usage"]`` dict, in either naming:
    ``input_tokens``/``output_tokens`` or OpenAI-style ``prompt_tokens``/``completion_tokens``."""
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    try:
        return int(input_tokens), int(output_tokens)
    except (TypeError, ValueError):
        return None


def _model_name(response_metadata: dict) -> str | None:
    return response_metadata.get("model_name") or response_metadata.get("model")


# deepagents' filesystem tools and the argument that names what they touched.
PATH_TOOLS = ("read_file", "ls", "glob", "grep")
_PATH_ARGS = ("file_path", "path")
MAX_TOOL_PATHS = 50


def _tool_args(input_str: str, inputs: object) -> dict | None:
    """The tool's arguments: the `inputs` kwarg when LangChain passes one, else `input_str` parsed
    as JSON or a Python literal; None when neither is a dict."""
    if isinstance(inputs, dict):
        return inputs
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(input_str)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
        return parsed if isinstance(parsed, dict) else None
    return None


def _tool_path(input_str: str, inputs: object) -> str | None:
    args = _tool_args(input_str, inputs)
    if args is None:
        return None
    for key in _PATH_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class UsageCollector(BaseCallbackHandler):
    """A LangChain callback that counts every chat-model call and tool call, including nested
    (sub-agent) runs. Safe to share across threads: appends are protected by a lock.

    It also records the path each file tool (`PATH_TOOLS`) was called on, in `tool_paths`: at most
    `MAX_TOOL_PATHS` distinct paths per tool, in call order. Pass `tool_paths` to record into a dict
    that outlives this collector (the engine's `CommandLog.tool_paths`).
    """

    def __init__(self, *, ignore_tools: Iterable[str] = (), tool_paths: dict[str, list[str]] | None = None) -> None:
        self._lock = threading.Lock()
        self.calls: list[ModelCall] = []
        self.tool_calls: dict[str, int] = {}
        self.tool_paths: dict[str, list[str]] = tool_paths if tool_paths is not None else {}
        self._ignore_tools = set(ignore_tools)

    def on_llm_end(self, response: LLMResult, *, run_id, parent_run_id=None, **kwargs) -> None:
        llm_output = response.llm_output or {}
        # The response-level aggregate, for generations whose message carries no usage. It is
        # for the whole response, so it is applied to one generation only (n > 1 would
        # otherwise count it n times).
        token_usage_fallback = _token_usage(llm_output.get("token_usage"))
        fallback_model = _model_name(llm_output)

        new_calls: list[ModelCall] = []
        for generation_list in response.generations:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                if message is None:
                    continue
                usage = _usage_from_message(message)
                if usage is None:
                    usage, token_usage_fallback = token_usage_fallback or (0, 0), None
                response_metadata = getattr(message, "response_metadata", None) or {}
                cost = response_metadata.get("cost")
                model = _model_name(response_metadata) or fallback_model
                new_calls.append(
                    ModelCall(
                        model=model,
                        input_tokens=usage[0],
                        output_tokens=usage[1],
                        reported_cost=float(cost) if cost is not None else None,
                        cache_read_tokens=_cache_reads(message),
                    )
                )
        if new_calls:
            with self._lock:
                self.calls.extend(new_calls)

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id, **kwargs) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name")
        if not name or name in self._ignore_tools:
            return
        path = _tool_path(input_str, kwargs.get("inputs")) if name in PATH_TOOLS else None
        with self._lock:
            self.tool_calls[name] = self.tool_calls.get(name, 0) + 1
            if path is not None:
                paths = self.tool_paths.setdefault(name, [])
                if path not in paths and len(paths) < MAX_TOOL_PATHS:
                    paths.append(path)
