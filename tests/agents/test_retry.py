import httpx
import openrouter.components.badrequestresponseerrordata as openrouter_badrequest_data
import openrouter.components.serviceunavailableresponseerrordata as openrouter_serviceunavailable_data
import openrouter.errors.badrequestresponse_error as openrouter_badrequest_error
import openrouter.errors.serviceunavailableresponse_error as openrouter_serviceunavailable_error
import pytest
from langchain_openrouter import ChatOpenRouter

from phil.agents.fake import FakeAgent, FakeAgentFactory
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.retry import call_with_retry, is_transient
from tests.agents.conftest import critique


def _openrouter_response(status: int, message: str, code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(status, request=request, json={"error": {"code": code, "message": message}})


def openrouter_service_unavailable() -> Exception:
    """A real `openrouter` SDK exception for a 503 response."""
    data = openrouter_serviceunavailable_error.ServiceUnavailableResponseErrorData(
        error=openrouter_serviceunavailable_data.ServiceUnavailableResponseErrorData(
            code=503, message="Service temporarily unavailable"
        )
    )
    return openrouter_serviceunavailable_error.ServiceUnavailableResponseError(
        data=data, raw_response=_openrouter_response(503, "Service temporarily unavailable", 503)
    )


def openrouter_bad_request() -> Exception:
    """A real `openrouter` SDK exception for a 400 response."""
    data = openrouter_badrequest_error.BadRequestResponseErrorData(
        error=openrouter_badrequest_data.BadRequestResponseErrorData(code=400, message="Provider returned error")
    )
    return openrouter_badrequest_error.BadRequestResponseError(
        data=data, raw_response=_openrouter_response(400, "Provider returned error", 400)
    )


def openrouter_200_with_error(code: int, message: str = "trouble upstream") -> Exception:
    """The plain `ValueError` `langchain_openrouter.ChatOpenRouter` raises when OpenRouter
    answers HTTP 200 with an error payload in the body."""
    model = ChatOpenRouter(model="openai/gpt-6-luna", api_key="test-key-not-used")
    with pytest.raises(ValueError) as excinfo:
        model._create_chat_result({"error": {"code": code, "message": message}})
    return excinfo.value


class HTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


def test_is_transient():
    assert is_transient(HTTPError(429))
    assert is_transient(HTTPError(503))
    assert is_transient(TimeoutError())
    assert not is_transient(HTTPError(401))
    assert not is_transient(ValueError("bad"))


def test_client_side_timeout_is_transient():
    # A model-call timeout (RunConfig.model_timeout_s elapsing) surfaces as a raw httpx timeout
    # from the openrouter SDK's underlying client, not a response with a status.
    assert is_transient(httpx.ReadTimeout("timed out waiting for a response"))


def test_openrouter_sdk_response_errors_are_transient_or_not_by_status():
    assert is_transient(openrouter_service_unavailable())
    assert not is_transient(openrouter_bad_request())


def test_openrouter_200_with_error_is_transient_by_payload_code():
    # OpenRouter can answer HTTP 200 with an error payload in the body (e.g. the upstream
    # provider failed after billing succeeded); langchain_openrouter surfaces that as a plain
    # ValueError with no status/response attribute at all.
    assert is_transient(openrouter_200_with_error(503))
    assert not is_transient(openrouter_200_with_error(400))


def test_retries_transient_errors_with_backoff():
    delays: list[float] = []
    agent = FakeAgent([HTTPError(429), HTTPError(503), "done"])
    result, retries = call_with_retry(agent, {"messages": []}, sleep=delays.append)
    assert result["structured_response"] == "done"
    assert delays == [1.0, 2.0]
    assert retries == 2


def test_gives_up_after_attempts():
    agent = FakeAgent([HTTPError(429), HTTPError(429), HTTPError(429)])
    with pytest.raises(HTTPError):
        call_with_retry(agent, {"messages": []}, sleep=lambda _: None)


def test_does_not_retry_permanent_errors():
    delays: list[float] = []
    agent = FakeAgent([HTTPError(401), "never"])
    with pytest.raises(HTTPError):
        call_with_retry(agent, {"messages": []}, sleep=delays.append)
    assert delays == []


def test_invoke_agent_retries_and_records_errors(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([HTTPError(429), critique()])
    ctx = AgentContext(
        config=config, conn=conn, layer="run", artifacts=artifacts, factory=factory, sleep=lambda _: None
    )
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    assert [row["outcome"] for row in conn.execute("SELECT outcome FROM telemetry")] == ["ok"]

    failing = FakeAgentFactory([HTTPError(401)])
    ctx.factory = failing
    with pytest.raises(HTTPError):
        invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    outcomes = [row["outcome"] for row in conn.execute("SELECT outcome FROM telemetry ORDER BY id")]
    assert outcomes == ["ok", "error"]


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.RemoteProtocolError("reset")],
)
def test_real_httpx_transport_errors_are_transient(exc):
    assert is_transient(exc)


def test_permanent_httpx_errors_are_not_transient():
    assert not is_transient(httpx.UnsupportedProtocol("ftp"))


def test_no_retries_reports_zero():
    result, retries = call_with_retry(FakeAgent(["done"]), {"messages": []}, sleep=lambda _: None)
    assert (result["structured_response"], retries) == ("done", 0)


def test_passes_config_to_the_agent_only_when_given():
    seen: list = []

    class Recorder:
        def invoke(self, payload, config=None):
            seen.append(config)
            return {"messages": []}

    call_with_retry(Recorder(), {"messages": []}, sleep=lambda _: None)
    call_with_retry(Recorder(), {"messages": []}, sleep=lambda _: None, config={"callbacks": []})
    assert seen == [None, {"callbacks": []}]


def test_invoke_agent_records_retry_count(config, conn, artifacts, critic_packet):
    factory = FakeAgentFactory([HTTPError(429), HTTPError(503), critique()])
    ctx = AgentContext(
        config=config, conn=conn, layer="run", artifacts=artifacts, factory=factory, sleep=lambda _: None
    )
    invoke_agent(get_spec("critic"), critic_packet, ctx, node="critic")
    assert [row["retries"] for row in conn.execute("SELECT retries FROM telemetry")] == [2]
