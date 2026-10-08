import anthropic
import httpx
import openai
import openrouter.components.serviceunavailableresponseerrordata as openrouter_serviceunavailable_data
import openrouter.errors.serviceunavailableresponse_error as openrouter_serviceunavailable_error
from langchain_google_genai.chat_models import GoogleAuthenticationError, GoogleRateLimitError

from phil.agents.failures import Failure, classify_failure
from phil.agents.invoke import ContractViolation
from phil.config import ConfigError


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
    assert f.action == "Details: /more 1"


def test_round_trip():
    f = classify_failure(HTTPError(401), provider="openai")
    assert Failure.from_dict(f.as_dict()) == f


def test_google_errors_carry_no_status():
    # langchain_google_genai raises these with no status/response attribute at all; the
    # classifier falls back to matching langchain_core's provider-neutral model-error names
    # along the exception's MRO (ruling: fix round 1, item 1).
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
