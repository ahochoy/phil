import anthropic
import httpx
import openai
import openrouter.components.serviceunavailableresponseerrordata as openrouter_serviceunavailable_data
import openrouter.errors.serviceunavailableresponse_error as openrouter_serviceunavailable_error
from langchain_core.exceptions import ModelAuthenticationError, ModelInvalidRequestError, ModelRateLimitError

from phil.agents.failures import Failure, classify_failure, internal_failure, tag_provider
from phil.agents.invoke import ContractViolation
from phil.agents.providers import BUILTIN_PROVIDERS, ProviderSpec
from phil.config import ConfigError


# Mirrors langchain_google_genai/chat_models.py's GoogleAuthenticationError/GoogleRateLimitError
# (subclassing the same langchain_core base classes, constructed the same way: a message only,
# no status) without importing langchain_google_genai itself, which pulls in google.genai and
# its own import-time DeprecationWarning ('_UnionGenericAlias' deprecated).
class GoogleAuthenticationError(ModelAuthenticationError):
    pass


class GoogleRateLimitError(ModelRateLimitError):
    pass


class GoogleInvalidRequestError(ModelInvalidRequestError):
    pass


class HTTPError(Exception):
    def __init__(self, status, message="boom"):
        super().__init__(message)
        self.status_code = status


class Response:
    def __init__(self, status):
        self.status_code = status


class ResponseError(Exception):
    def __init__(self, status):
        super().__init__("bad")
        self.response = Response(status)


def test_auth():
    f = classify_failure(HTTPError(401), provider="openrouter")
    assert f.category == "auth"
    assert f.headline == "The provider rejected your API key."
    assert f.action == "Run `phil keys set openrouter`, or set OPENROUTER_API_KEY."


def test_quota_by_status_and_by_wording():
    assert classify_failure(HTTPError(402), provider="openrouter").category == "quota"
    assert classify_failure(HTTPError(400, "Insufficient credits on this account"), provider="openrouter").category == "quota"
    f = classify_failure(HTTPError(402), provider="openrouter")
    assert f.headline == "Your openrouter account is out of credits."


def test_busy_after_retries():
    f = classify_failure(ResponseError(429), provider="anthropic", attempts=4)
    assert (f.category, f.headline, f.retries) == ("busy", "anthropic is busy. Phil retried 4 times.", "Phil already retried.")


def test_network():
    class APIConnectionError(Exception):
        pass
    APIConnectionError.__module__ = "openai"
    f = classify_failure(APIConnectionError("down"), provider="openai")
    assert f.category == "network" and f.headline == "Couldn't reach openai. Phil retried."


def test_refused_carries_the_detail():
    f = classify_failure(HTTPError(400, "model not found"), provider="openrouter")
    assert f.category == "refused"
    assert f.headline.startswith("openrouter refused the request: ")
    assert f.action == "Check the model with `phil models check`."


def test_output_and_config():
    f = classify_failure(ContractViolation("architect", ["no structured output was returned"]))
    assert f.category == "output"
    assert f.headline == "The architect didn't return a usable answer."
    assert f.action == "Try again, or use a stronger model for architect."
    c = classify_failure(ConfigError("No model for critic (tier high)."))
    assert (c.category, c.headline, c.action) == ("config", "No model for critic (tier high).", "Fix the setting it names.")


def test_anything_else_is_internal_and_never_raises():
    class Weird(Exception):
        @property
        def status_code(self):
            raise RuntimeError("nope")

    f = classify_failure(Weird("x"))
    assert f.category == "internal"
    assert f.headline == "Something went wrong inside Phil (Weird)."
    assert f.action == "Try again."  # the callout's own Details row points at the raw error
    assert f == internal_failure("Weird")


def test_without_a_provider_quota_reads_your_provider_account():
    f = classify_failure(HTTPError(402))
    assert f.headline == "Your provider account is out of credits."


def test_without_a_provider_auth_points_at_keys_list():
    f = classify_failure(HTTPError(401))
    assert f.headline == "The provider rejected your API key."
    assert f.action == "Check your API keys with `phil keys list`."


def test_without_a_provider_busy_server_network_and_refused_start_with_the_provider():
    class APIConnectionError(Exception):
        pass
    APIConnectionError.__module__ = "openai"
    assert classify_failure(ResponseError(429), attempts=3).headline == "The provider is busy. Phil retried 3 times."
    assert classify_failure(ResponseError(429)).headline == "The provider is busy. Phil retried."
    assert classify_failure(HTTPError(500)).headline == "The provider had a server error. Phil retried."
    assert classify_failure(APIConnectionError("down")).headline == "Couldn't reach the provider. Phil retried."
    assert classify_failure(HTTPError(400, "model not found")).headline == (
        "The provider refused the request: model not found"
    )


def test_no_headline_says_the_provider_mid_word_or_your_the():
    for exc in (HTTPError(401), HTTPError(402), ResponseError(429), HTTPError(500), HTTPError(400, "nope")):
        f = classify_failure(exc)
        text = f"{f.headline} {f.action}"
        assert "the provider account" not in text and "keys set the provider" not in text
        assert not f.headline.startswith("the ")


def test_round_trip():
    f = classify_failure(HTTPError(401), provider="openai")
    assert Failure.from_dict(f.as_dict()) == f


def test_google_errors_carry_no_status():
    # langchain_google_genai raises these with no status/response attribute at all (see the
    # GoogleAuthenticationError/GoogleRateLimitError stand-ins above); the classifier falls back
    # to matching langchain_core's provider-neutral model-error names along the exception's MRO
    # (ruling: fix round 1, item 1).
    assert classify_failure(GoogleAuthenticationError("bad key"), provider="google").category == "auth"
    f = classify_failure(GoogleRateLimitError("slow down"), provider="google", attempts=2)
    assert f.category == "busy"
    assert f.headline == "google is busy. Phil retried 2 times."


def test_server_category_for_5xx():
    f = classify_failure(HTTPError(500), provider="openai")
    assert f.category == "server"
    assert f.headline == "openai had a server error. Phil retried."
    assert f.retries == "Phil already retried."
    assert f.action == "Try again in a few minutes."
    assert classify_failure(HTTPError(503), provider="openai").category == "server"


def test_quota_phrases_do_not_false_positive():
    assert classify_failure(HTTPError(400, "model billing-assistant not found"), provider="openrouter").category == "refused"
    assert classify_failure(HTTPError(400, "payment field invalid"), provider="openrouter").category == "refused"


def _httpx_response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://x"))


def test_real_openai_and_anthropic_exceptions():
    auth = openai.AuthenticationError("bad key", response=_httpx_response(401), body=None)
    assert classify_failure(auth, provider="openai").category == "auth"
    server = openai.InternalServerError("boom", response=_httpx_response(500), body=None)
    assert classify_failure(server, provider="openai").category == "server"
    busy = anthropic.RateLimitError("slow down", response=_httpx_response(429), body=None)
    assert classify_failure(busy, provider="anthropic").category == "busy"


def test_real_openrouter_server_error():
    # Constructible offline (data model is plain pydantic; no network call in __init__), the
    # same way tests/agents/test_retry.py's `openrouter_service_unavailable` fixture builds it.
    data = openrouter_serviceunavailable_error.ServiceUnavailableResponseErrorData(
        error=openrouter_serviceunavailable_data.ServiceUnavailableResponseErrorData(
            code=503, message="Service temporarily unavailable"
        )
    )
    exc = openrouter_serviceunavailable_error.ServiceUnavailableResponseError(
        data=data, raw_response=_httpx_response(503)
    )
    assert classify_failure(exc, provider="openrouter").category == "server"


def _refused_by_the_sdk(content, role="tool") -> Exception:
    """The ValidationError the OpenRouter SDK raises, offline, when a message's content isn't in its
    form (here: a LangChain block it doesn't know), before any request is sent."""
    from typing import List

    from openrouter import components
    from openrouter.utils.serializers import unmarshal

    message = {"role": role, "content": content}
    if role == "tool":
        message["tool_call_id"] = "c1"
    try:
        unmarshal([{"role": "user", "content": "hi"}, message], List[components.ChatMessages])
    except Exception as exc:
        return exc
    raise AssertionError("the SDK accepted it")


def test_content_the_openrouter_sdk_wont_send_is_named_not_an_internal_error():
    image = _refused_by_the_sdk([{"type": "image", "base64": "/9j/", "mime_type": "image/jpeg"}])
    assert type(image).__name__ == "ValidationError"
    assert classify_failure(image, provider="openrouter") == Failure(
        "refused", "Phil couldn't send the request: openrouter doesn't accept an image in a tool result.",
        "Phil won't retry this.", "Try again without that file, or try a different model.",
    )
    audio = _refused_by_the_sdk([{"type": "audio", "base64": "AAAA", "mime_type": "audio/wav"}], role="user")
    assert classify_failure(audio).headline == (
        "Phil couldn't send the request: the provider doesn't accept an audio clip in your message."
    )


def test_any_other_validation_error_is_still_internal():
    from pydantic import BaseModel, ValidationError

    class Point(BaseModel):
        x: int

    try:
        Point(x="nope")
    except ValidationError as exc:
        assert classify_failure(exc, provider="openrouter") == internal_failure("ValidationError")


def test_a_custom_providers_auth_failure_names_its_own_key_variable():
    exc = HTTPError(401)
    tag_provider(exc, ProviderSpec("vllm", "openai", "http://box:8000/v1", "VLLM_API_KEY", None, None))
    f = classify_failure(exc)  # the run worker passes no provider
    assert f.category == "auth"
    assert f.action == "Run `phil keys set vllm`, or set VLLM_API_KEY."


def test_an_overridden_key_variable_is_named():
    exc = HTTPError(401)
    tag_provider(exc, ProviderSpec("openai", "openai", None, "WORK_OPENAI_KEY", None, None))
    assert classify_failure(exc, provider="openai").action == "Run `phil keys set openai`, or set WORK_OPENAI_KEY."


def test_a_keyless_provider_gets_no_keys_set_hint():
    exc = HTTPError(401)
    tag_provider(exc, BUILTIN_PROVIDERS["ollama"])
    f = classify_failure(exc)
    assert "phil keys set" not in f.action
    assert f.action == "Check [providers.ollama] in your config: it sends no API key."


def test_an_untagged_builtin_still_gets_its_default_key():
    assert classify_failure(HTTPError(401), provider="anthropic").action == (
        "Run `phil keys set anthropic`, or set ANTHROPIC_API_KEY."
    )


def test_an_untagged_unknown_provider_points_at_keys_list():
    assert classify_failure(HTTPError(401), provider="mystery").action == "Check your API keys with `phil keys list`."


def test_googles_bad_key_400_is_an_auth_failure():
    exc = GoogleInvalidRequestError(
        "Error calling model 'gemini-3.8-flash' (INVALID_ARGUMENT): 400 INVALID_ARGUMENT. "
        "{'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.', "
        "'status': 'INVALID_ARGUMENT', 'details': [{'reason': 'API_KEY_INVALID'}]}}"
    )
    f = classify_failure(exc, provider="google")
    assert f.category == "auth"
    assert f.action == "Run `phil keys set google`, or set GOOGLE_API_KEY."


def test_a_local_server_that_is_down_says_so():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"))
    tag_provider(exc, BUILTIN_PROVIDERS["ollama"])
    f = classify_failure(exc)
    assert f.category == "network"
    assert f.headline == "Couldn't reach ollama at http://localhost:11434/v1."
    assert f.retries == "Phil won't retry this."
    assert f.action == "Is `ollama serve` running?"


def test_another_local_server_that_is_down_names_its_url():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:8000/v1/chat/completions"))
    tag_provider(exc, ProviderSpec("vllm", "openai", "http://localhost:8000/v1", None, None, None))
    f = classify_failure(exc)
    assert f.headline == "Couldn't reach vllm at http://localhost:8000/v1."
    assert f.action == "Is the server at http://localhost:8000/v1 running?"


def test_an_untagged_local_server_uses_the_request_url():
    exc = openai.APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:9000/v1/x"))
    f = classify_failure(exc, provider="lab")
    assert f.headline == "Couldn't reach lab at http://127.0.0.1:9000."


def test_googles_quota_error_is_quota_not_busy():
    f = classify_failure(GoogleRateLimitError("You exceeded your current quota."), provider="google")
    assert f.category == "quota"
