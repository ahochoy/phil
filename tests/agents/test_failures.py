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
