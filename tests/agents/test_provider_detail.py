"""A provider's own reason for a rejected request reaches Phil's error message (OpenRouter puts it
in `error.metadata`; the SDK's message alone is "Provider returned error")."""

import json

import httpx
import openrouter.components.badrequestresponseerrordata as badrequest_data
import openrouter.errors.badrequestresponse_error as badrequest_error
import pytest

from phil.agents.model_retry import PhilModelRetryMiddleware
from phil.agents.retry import provider_detail

ANTHROPIC_RAW = json.dumps(
    {"type": "error", "error": {"type": "invalid_request_error", "message": "tool_choice is not supported"}}
)


def bad_request(metadata: dict | None) -> badrequest_error.BadRequestResponseError:
    """A real `openrouter` SDK error for a 400 whose body carries `metadata` as OpenRouter sends it."""
    error = {"code": 400, "message": "Provider returned error"}
    if metadata is not None:
        error["metadata"] = metadata
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"error": error})
    data = badrequest_error.BadRequestResponseErrorData(
        error=badrequest_data.BadRequestResponseErrorData(code=400, message="Provider returned error")
    )
    return badrequest_error.BadRequestResponseError(data=data, raw_response=response)


def test_the_provider_and_its_reason_are_read_from_the_body():
    exc = bad_request({"provider_name": "Anthropic", "raw": ANTHROPIC_RAW})
    assert provider_detail(exc) == "Anthropic: tool_choice is not supported"


def test_a_plain_text_reason_is_kept_and_clipped():
    exc = bad_request({"provider_name": "Google", "raw": "model  does not\nsupport tools " + "x" * 500})
    detail = provider_detail(exc)
    assert detail.startswith("Google: model does not support tools x") and len(detail) <= len("Google: ") + 300


def test_no_metadata_or_no_body_gives_nothing():
    assert provider_detail(bad_request(None)) is None
    assert provider_detail(bad_request({"provider_name": "Anthropic"})) is None
    assert provider_detail(ValueError("boom")) is None


def test_the_middleware_adds_the_reason_to_a_rejected_call_once():
    exc = bad_request({"provider_name": "Anthropic", "raw": ANTHROPIC_RAW})

    def handler(request):
        raise exc

    with pytest.raises(badrequest_error.BadRequestResponseError) as raised:
        PhilModelRetryMiddleware().wrap_model_call(object(), handler)
    assert str(raised.value) == "Provider returned error (Anthropic: tool_choice is not supported)"
    with pytest.raises(badrequest_error.BadRequestResponseError):
        PhilModelRetryMiddleware().wrap_model_call(object(), handler)  # the same error again isn't doubled
    assert str(exc) == "Provider returned error (Anthropic: tool_choice is not supported)"
