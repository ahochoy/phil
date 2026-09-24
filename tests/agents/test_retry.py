import httpx
import pytest

from phil.agents.fake import FakeAgent, FakeAgentFactory
from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.agents.retry import call_with_retry, is_transient
from tests.agents.conftest import critique


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


def test_retries_transient_errors_with_backoff():
    delays: list[float] = []
    agent = FakeAgent([HTTPError(429), HTTPError(503), "done"])
    result = call_with_retry(agent, {"messages": []}, sleep=delays.append)
    assert result["structured_response"] == "done"
    assert delays == [1.0, 2.0]


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
