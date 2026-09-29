import re
from collections.abc import Callable
from typing import Any

TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}

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
    if any(cls.__module__.split(".")[0] == "httpx" and cls.__name__ in _TIMEOUT_HTTPX for cls in type(exc).__mro__):
        return True
    return any(
        cls.__module__.split(".")[0] == "openrouter" and cls.__name__ in _TIMEOUT_OPENROUTER
        for cls in type(exc).__mro__
    )


def _200_with_error_code(exc: BaseException) -> int | None:
    match = _OPENROUTER_200_WITH_ERROR.search(str(exc))
    return int(match.group(1)) if match else None


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if _is_httpx_transient(exc) or _is_openrouter_transient(exc):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in TRANSIENT_STATUS:
        return True
    if status is None or status == 200:
        code = _200_with_error_code(exc)
        if code is not None:
            return code in TRANSIENT_STATUS
    return False


def call_with_retry(
    agent: Any,
    payload: dict,
    *,
    sleep: Callable[[float], None],
    attempts: int = 3,
    base_delay: float = 1.0,
    config: dict | None = None,
) -> tuple[dict, int]:
    """Invoke ``agent`` with transient-error retries; returns ``(result, retries)``.

    A timeout gets at most 2 tries total regardless of ``attempts``: a stuck provider otherwise
    costs one ``model_timeout_s`` per attempt (up to ~3x that with the default ``attempts=3``).
    Other transient errors (rate limits, 5xx, network resets) keep the full ``attempts`` budget.
    """
    for index in range(attempts):
        try:
            result = agent.invoke(payload) if config is None else agent.invoke(payload, config=config)
            return result, index
        except Exception as exc:
            if not is_transient(exc):
                raise
            max_index = 1 if is_timeout(exc) else attempts - 1
            if index >= max_index:
                raise
            sleep(base_delay * 2**index)
    raise AssertionError("unreachable")
