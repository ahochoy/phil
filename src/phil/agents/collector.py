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


def _usage_from_message(message: object) -> tuple[int, int] | None:
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return None
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def _model_name(response_metadata: dict) -> str | None:
    return response_metadata.get("model_name") or response_metadata.get("model")


class UsageCollector(BaseCallbackHandler):
    """A LangChain callback that counts every chat-model call and tool call, including nested
    (sub-agent) runs. Safe to share across threads: appends are protected by a lock.
    """

    def __init__(self, *, ignore_tools: Iterable[str] = ()) -> None:
        self._lock = threading.Lock()
        self.calls: list[ModelCall] = []
        self.tool_calls: dict[str, int] = {}
        self._ignore_tools = set(ignore_tools)

    def on_llm_end(self, response: LLMResult, *, run_id, parent_run_id=None, **kwargs) -> None:
        token_usage_fallback = None
        llm_output = response.llm_output or {}
        if isinstance(llm_output.get("token_usage"), dict):
            usage = llm_output["token_usage"]
            token_usage_fallback = (int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0))
        fallback_model = _model_name(llm_output)

        new_calls: list[ModelCall] = []
        for generation_list in response.generations:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                if message is None:
                    continue
                usage = _usage_from_message(message) or token_usage_fallback or (0, 0)
                response_metadata = getattr(message, "response_metadata", None) or {}
                cost = response_metadata.get("cost")
                model = _model_name(response_metadata) or fallback_model
                new_calls.append(
                    ModelCall(
                        model=model,
                        input_tokens=usage[0],
                        output_tokens=usage[1],
                        reported_cost=float(cost) if cost is not None else None,
                    )
                )
        if new_calls:
            with self._lock:
                self.calls.extend(new_calls)

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id, **kwargs) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name")
        if not name or name in self._ignore_tools:
            return
        with self._lock:
            self.tool_calls[name] = self.tool_calls.get(name, 0) + 1
