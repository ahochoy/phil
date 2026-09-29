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
    """Invoke ``agent`` with transient-error retries; returns ``(result, retries)``."""
    for index in range(attempts):
        try:
            result = agent.invoke(payload) if config is None else agent.invoke(payload, config=config)
            return result, index
        except Exception as exc:
            if not is_transient(exc) or index == attempts - 1:
                raise
            sleep(base_delay * 2**index)
    raise AssertionError("unreachable")
