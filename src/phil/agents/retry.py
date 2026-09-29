import re
from collections.abc import Callable
from typing import Any

TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}  # 529: Anthropic "overloaded"

_TRANSIENT_HTTPX = {"TimeoutException", "NetworkError", "RemoteProtocolError"}

# openrouter SDK response-error classes (openrouter.errors.*) for timeouts and 5xx/429
# responses. `OpenRouterError.status_code` (set from the HTTP response) already carries a
# transient status for all of these, so the generic status-code check below already recognizes
# them; matching the class names too — the same way `_is_httpx_transient` matches httpx's —
# keeps recognition working even if a future SDK version stops setting `status_code`.
_TRANSIENT_OPENROUTER = {
    "RequestTimeoutResponseError",  # 408: operation exceeded time limit
    "EdgeNetworkTimeoutResponseError",  # 504: provider request timed out at the edge network
    "BadGatewayResponseError",  # 502: provider/upstream API failure
    "ServiceUnavailableResponseError",  # 503: service temporarily unavailable
    "TooManyRequestsResponseError",  # 429: rate limit exceeded
    "ProviderOverloadedResponseError",  # 5xx: provider temporarily overloaded
    "InternalServerResponseError",  # 500
}

# langchain_openrouter's ChatOpenRouter._create_chat_result raises a plain ValueError when
# OpenRouter answers with HTTP 200 but an error payload in the body (the request succeeded but
# the upstream provider failed after billing), formatted as
# "OpenRouter API returned an error: <message> (code: <code>)". That exception carries no
# status/response attribute at all, so it reads as status None ("200 or none" below) and the
# transience decision falls back to the error payload's own `code`.
_OPENROUTER_200_WITH_ERROR = re.compile(r"OpenRouter API returned an error:.*\(code:\s*(\d+)\)\s*$")

# The anthropic and openai SDKs raise their own classes (not httpx's) for a request that timed
# out or never connected; with the SDKs' own retries turned off (phil.agents.factory) these reach
# Phil directly. `APITimeoutError` subclasses `APIConnectionError` in both SDKs.
_SDK_MODULES = {"anthropic", "openai"}
_TRANSIENT_SDK = {"APITimeoutError", "APIConnectionError"}
_TIMEOUT_SDK = {"APITimeoutError"}

# langchain_core's provider-neutral model errors (langchain_core.exceptions), which integrations
# raise alongside their SDK's own types — langchain_google_genai raises a bare
# `GoogleRateLimitError(msg)` for a 429, with no status attribute at all. Matched by class name so
# this module needs no langchain import. `ModelAPIError` (a provider-reported server failure) is
# transient only when its status says so (`_status` below): a 501 isn't worth retrying.
_TRANSIENT_CORE = {"ModelRateLimitError", "ModelConnectionError", "ModelTimeoutError"}
_TIMEOUT_CORE = {"ModelTimeoutError"}

# The subset of the transient classes above that specifically mean "the call timed out" rather
# than some other transient failure (rate limit, 5xx, network reset). httpx.TimeoutException
# covers ConnectTimeout/ReadTimeout/WriteTimeout/PoolTimeout via subclassing.
_TIMEOUT_HTTPX = {"TimeoutException"}
_TIMEOUT_OPENROUTER = {"RequestTimeoutResponseError", "EdgeNetworkTimeoutResponseError"}


def _is_httpx_transient(exc: BaseException) -> bool:
    return any(
        cls.__module__.split(".")[0] == "httpx" and cls.__name__ in _TRANSIENT_HTTPX
        for cls in type(exc).__mro__
    )


def _matches(exc: BaseException, modules: set[str], names: set[str]) -> bool:
    return any(cls.__module__.split(".")[0] in modules and cls.__name__ in names for cls in type(exc).__mro__)


def _is_openrouter_transient(exc: BaseException) -> bool:
    return any(
        cls.__module__.split(".")[0] == "openrouter" and cls.__name__ in _TRANSIENT_OPENROUTER
        for cls in type(exc).__mro__
    )


def is_timeout(exc: BaseException) -> bool:
    """True for the transient errors that mean the call itself timed out (a stuck provider),
    as opposed to a rate limit, a 5xx, or a network reset. `call_with_retry` gives these fewer
    attempts: a stuck provider otherwise costs one `model_timeout_s` per attempt."""
    if isinstance(exc, TimeoutError):
        return True
    return (
        _matches(exc, {"httpx"}, _TIMEOUT_HTTPX)
        or _matches(exc, {"openrouter"}, _TIMEOUT_OPENROUTER)
        or _matches(exc, _SDK_MODULES, _TIMEOUT_SDK)
        or _matches(exc, {"langchain_core"}, _TIMEOUT_CORE)
    )


def _200_with_error_code(exc: BaseException) -> int | None:
    match = _OPENROUTER_200_WITH_ERROR.search(str(exc))
    return int(match.group(1)) if match else None


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if _is_httpx_transient(exc) or _is_openrouter_transient(exc) or _matches(exc, _SDK_MODULES, _TRANSIENT_SDK):
        return True
    if _matches(exc, {"langchain_core"}, _TRANSIENT_CORE):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None and _matches(exc, {"langchain_core"}, {"ModelAPIError"}):
        code = getattr(exc, "code", None)  # e.g. google.genai's APIError.code
        status = code if isinstance(code, int) else None
    if status in TRANSIENT_STATUS:
        return True
    if status is None or status == 200:
        code = _200_with_error_code(exc)
        if code is not None:
            return code in TRANSIENT_STATUS
    return False


MODEL_CALL_ATTEMPTS = 3
BASE_DELAY_S = 1.0


def retry_delay(
    exc: BaseException, index: int, *, attempts: int = MODEL_CALL_ATTEMPTS, base_delay: float = BASE_DELAY_S
) -> float | None:
    """Phil's retry policy for one failed try (``index`` is 0 for the first try): the backoff
    to sleep before trying again, or None when ``exc`` must propagate.

    Only transient errors are retried. A timeout gets at most 2 tries total regardless of
    ``attempts``: a stuck provider otherwise costs one ``model_timeout_s`` per attempt. Other
    transient errors (rate limits, 5xx, network resets) get the full ``attempts`` budget.
    """
    if not is_transient(exc):
        return None
    max_index = min(1, attempts - 1) if is_timeout(exc) else attempts - 1
    if index >= max_index:
        return None
    return base_delay * 2**index


def call_with_retry(
    agent: Any,
    payload: dict,
    *,
    sleep: Callable[[float], None],
    attempts: int = MODEL_CALL_ATTEMPTS,
    base_delay: float = BASE_DELAY_S,
    config: dict | None = None,
    retryable: Callable[[BaseException], bool] = lambda exc: True,
) -> tuple[dict, int]:
    """Invoke ``agent`` with transient-error retries (policy: `retry_delay`); returns
    ``(result, retries)``. ``retryable`` can veto a retry for an error that is transient but
    was already retried elsewhere (see `phil.agents.model_retry`)."""
    for index in range(attempts):
        try:
            result = agent.invoke(payload) if config is None else agent.invoke(payload, config=config)
            return result, index
        except Exception as exc:
            delay = retry_delay(exc, index, attempts=attempts, base_delay=base_delay) if retryable(exc) else None
            if delay is None:
                raise
            sleep(delay)
    raise AssertionError("unreachable")
