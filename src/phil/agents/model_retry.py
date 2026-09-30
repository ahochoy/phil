"""Per-model-call retries, as agent middleware (imports langchain: load lazily).

The provider SDKs' own retries are off (phil.agents.factory), so a transient error on one
model call would otherwise propagate out of the agent and re-run the whole agent: earlier model
calls paid twice, shell/file tools re-run on an already-changed worktree. This middleware
retries just the failed model call, with Phil's policy (`phil.agents.retry.retry_delay`).

langchain's built-in `ModelRetryMiddleware` can't express that policy (one retry budget for
every error, no separate cap for timeouts), sleeps with `time.sleep` directly, and doesn't
report how many retries it made; hence this small middleware.

Per-invocation state — the sleep to use and where to count retries — comes from the run's
config (``config["configurable"][TRACKER_KEY]``), which LangGraph passes down to nested runs,
deep-agent sub-agents included. Without a tracker it sleeps with `time.sleep` and counts
nothing.
"""

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.errors import GraphBubbleUp

from phil.agents.retry import MODEL_CALL_ATTEMPTS, retry_delay

TRACKER_KEY = "phil_model_retry"
_RETRIED_ATTR = "_phil_model_call_retried"


class ModelRetryTracker:
    """Shared by every model call in one agent invocation (sub-agents too): the sleep to back
    off with, the tries each model call gets (1: no retries), and the number of retries made.
    Thread-safe: sub-agents can run in parallel."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep, attempts: int = MODEL_CALL_ATTEMPTS) -> None:
        self.sleep = sleep
        self.attempts = attempts
        self._lock = threading.Lock()
        self.retries = 0

    def retried(self) -> None:
        with self._lock:
            self.retries += 1


def model_call_retried(exc: BaseException) -> bool:
    """True when ``exc`` came out of a model call this middleware already retried as far as the
    policy allows (or declined to retry): retrying the whole agent for it again would repeat
    every earlier model call and tool call."""
    return bool(getattr(exc, _RETRIED_ATTR, False))


def _mark(exc: BaseException) -> None:
    try:
        setattr(exc, _RETRIED_ATTR, True)
    except (AttributeError, TypeError):  # an exception type without a __dict__
        pass


def _tracker() -> ModelRetryTracker | None:
    from langgraph.config import get_config

    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:  # not inside a runnable
        return None
    tracker = configurable.get(TRACKER_KEY)
    return tracker if isinstance(tracker, ModelRetryTracker) else None


def _attempts(tracker: ModelRetryTracker | None) -> int:
    return tracker.attempts if tracker is not None else MODEL_CALL_ATTEMPTS


class PhilModelRetryMiddleware(AgentMiddleware):
    """Retry a failed model call when `retry_delay` says so; otherwise let the error propagate,
    marked so `call_with_retry` doesn't re-run the whole agent for it."""

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        tracker = _tracker()
        index = 0
        while True:
            try:
                return handler(request)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                delay = retry_delay(exc, index, attempts=_attempts(tracker))
                if delay is None:
                    _mark(exc)
                    raise
                if tracker is not None:
                    tracker.retried()
                (tracker.sleep if tracker is not None else time.sleep)(delay)
                index += 1

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        tracker = _tracker()
        index = 0
        while True:
            try:
                return await handler(request)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                delay = retry_delay(exc, index, attempts=_attempts(tracker))
                if delay is None:
                    _mark(exc)
                    raise
                if tracker is not None:
                    tracker.retried()
                    await asyncio.to_thread(tracker.sleep, delay)  # a blocking sleep: keep it off the loop
                else:
                    await asyncio.sleep(delay)
                index += 1
