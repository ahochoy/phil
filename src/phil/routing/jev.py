"""TypeSafe Jev over plain HTTP (spec §3.4): one attempt, no retries, the key never shown."""

import time
from collections.abc import Callable, Mapping
from typing import Any

from phil.agents.providers import ProviderSpec
from phil.key_store import key_lookup
from phil.routing.classes import (
    CLASSES,
    NEEDS_DETAIL_CRITERIA,
    NEEDS_DETAIL_QUESTION,
    TASK_CLASS_QUESTION,
)
from phil.routing.types import Judgement, RouteState, Usage


class JevError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_request(model_name: str, state: RouteState) -> dict:
    return {
        "model": model_name,
        "state": state,
        "questions": {
            "task_class": {
                "type": "choice",
                "instructions": TASK_CLASS_QUESTION,
                "criteria": {
                    key: {"description": info.description, "examples": list(info.examples)}
                    for key, info in CLASSES.items()
                },
            },
            "needs_detail": {
                "type": "noul",
                "instructions": NEEDS_DETAIL_QUESTION,
                "criteria": NEEDS_DETAIL_CRITERIA,
            },
        },
    }


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise JevError("malformed")
    return float(value)


def _token_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise JevError("malformed")
    return int(value)


def parse_response(body: object) -> tuple[str, dict[str, float], float, float, Usage | None]:
    """(choice, probabilities, confidence, needs_detail, usage); raises JevError("malformed")."""
    try:
        answers = body["answers"]  # type: ignore[index]
        task_class = answers["task_class"]
        choice = task_class["choice"]
        noul = answers["needs_detail"]["noul"]
        probabilities = task_class.get("probabilities") or {}
        confidence = task_class["confidence"]
    except (KeyError, TypeError):
        raise JevError("malformed") from None
    if not isinstance(choice, str) or choice not in CLASSES or not isinstance(probabilities, dict):
        raise JevError("malformed")
    probs = {str(k): _probability(v) for k, v in probabilities.items()}
    usage = None
    raw_usage = body.get("usage") if isinstance(body, dict) else None
    if isinstance(raw_usage, dict):
        input_tokens = _token_count(raw_usage.get("input_tokens", 0))
        output_tokens = _token_count(raw_usage.get("output_tokens", 0))
        cost = raw_usage.get("cost")
        if cost is not None and (isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0):
            raise JevError("malformed")
        usage = Usage(input_tokens, output_tokens, float(cost) if cost is not None else None)
    return choice, probs, _probability(confidence), _probability(noul), usage


def _post(
    spec: ProviderSpec, body: dict, *, timeout_s: float, environ: Mapping[str, str] | None, transport: object | None
) -> Any:
    import httpx

    env = environ if environ is not None else key_lookup()
    key = env.get(spec.api_key_env or "") or ""
    if not key:
        raise JevError("missing key")
    url = f"{(spec.base_url or '').rstrip('/')}/systemone"
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as client:  # type: ignore[arg-type]
            response = client.post(
                url, json=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            )
    except httpx.TimeoutException:
        raise JevError("timeout") from None
    except httpx.HTTPError:
        raise JevError("network") from None
    except Exception:
        # Anything else building or sending the request (e.g. a key with a non-ASCII character makes
        # the header unencodable). Its message could quote the key, so none of it survives.
        raise JevError("request failed") from None
    if response.status_code != 200:
        raise JevError(f"http {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise JevError("malformed") from None


def judge_jev(
    spec: ProviderSpec,
    model_name: str,
    state: RouteState,
    *,
    timeout_s: float,
    environ: Mapping[str, str] | None = None,
    transport: object | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Judgement:
    started = clock()
    body = _post(spec, build_request(model_name, state), timeout_s=timeout_s, environ=environ, transport=transport)
    choice, probabilities, confidence, needs_detail, usage = parse_response(body)
    return Judgement(
        task_class=choice, probabilities=probabilities, confidence=confidence, needs_detail=needs_detail,
        source="jev", latency_ms=int((clock() - started) * 1000), usage=usage,
    )


def ping_jev(
    spec: ProviderSpec,
    model_name: str,
    *,
    timeout_s: float,
    environ: Mapping[str, str] | None = None,
    transport: object | None = None,
) -> None:
    """One tiny request; raises JevError if Jev can't answer it."""
    body = {
        "model": model_name,
        "state": {"text": "ping"},
        "questions": {
            "ping": {"type": "choice", "instructions": "Is `text` the word ping?",
                     "criteria": {"yes": "It is.", "no": "It isn't."}}
        },
    }
    answer = _post(spec, body, timeout_s=timeout_s, environ=environ, transport=transport)
    try:
        if answer["answers"]["ping"]["choice"] not in ("yes", "no"):
            raise JevError("malformed")
    except (KeyError, TypeError):
        raise JevError("malformed") from None
