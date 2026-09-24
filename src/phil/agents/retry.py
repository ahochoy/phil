from collections.abc import Callable
from typing import Any

TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in TRANSIENT_STATUS


def call_with_retry(
    agent: Any,
    payload: dict,
    *,
    sleep: Callable[[float], None],
    attempts: int = 3,
    base_delay: float = 1.0,
) -> dict:
    for index in range(attempts):
        try:
            return agent.invoke(payload)
        except Exception as exc:
            if not is_transient(exc) or index == attempts - 1:
                raise
            sleep(base_delay * 2**index)
    raise AssertionError("unreachable")
