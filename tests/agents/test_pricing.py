import json
from pathlib import Path

import pytest

from phil.agents.pricing import RETRY_AFTER_FAILURE_S, Price, PriceBook
from phil.agents.pricing import _default_fetch as real_default_fetch  # before the autouse guard swaps it

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "openrouter-models.json").read_text())


def book(tmp_path, fetch=lambda: FIXTURE, now=1_000_000.0):
    return PriceBook(tmp_path / "models.json", fetch=fetch, clock=lambda: now)


def test_prices_openrouter_models(tmp_path):
    b = book(tmp_path)
    price = b.price("openrouter:openai/gpt-6-sol")
    assert price == Price(prompt=0.000002, completion=0.00001)
    assert b.estimate("openrouter:openai/gpt-6-sol", 1000, 100) == pytest.approx(0.003)
    assert b.price("openrouter:nope/unknown") is None
    assert b.price("anthropic:claude-sonnet-5") is None


def test_uses_a_fresh_cache_without_fetching(tmp_path):
    book(tmp_path).price("openrouter:openai/gpt-6-sol")  # writes the cache

    def boom():
        raise AssertionError("fetched")

    assert book(tmp_path, fetch=boom, now=1_000_000.0 + 60).price("openrouter:openai/gpt-6-sol") is not None


def test_refreshes_a_stale_cache_and_falls_back_on_failure(tmp_path):
    book(tmp_path).price("openrouter:openai/gpt-6-sol")
    calls = []

    def failing():
        calls.append(1)
        raise OSError("offline")

    later = book(tmp_path, fetch=failing, now=1_000_000.0 + 2 * 86400)
    assert later.price("openrouter:openai/gpt-6-sol") is not None  # stale cache used
    assert calls == [1]


def test_no_cache_and_no_network_means_no_prices(tmp_path):
    def failing():
        raise OSError("offline")

    assert book(tmp_path, fetch=failing).estimate("openrouter:openai/gpt-6-sol", 10, 10) is None


def test_default_retry_after_failure_is_ten_minutes():
    assert RETRY_AFTER_FAILURE_S == 10 * 60


def test_retries_after_a_cooldown_when_a_first_fetch_fails_with_no_cache(tmp_path):
    # With no cache at all, a failed first fetch must not leave the (process-wide) book empty
    # forever: it should retry once a cool-down has elapsed, in case the network is back.
    calls: list[int] = []

    def fetch():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise OSError("offline")
        return FIXTURE

    clock = {"now": 1_000_000.0}
    b = PriceBook(
        tmp_path / "models.json", fetch=fetch, clock=lambda: clock["now"], retry_after_failure_s=600.0
    )

    assert b.estimate("openrouter:openai/gpt-6-sol", 1000, 100) is None
    assert calls == [1]

    clock["now"] += 599  # still cooling down: no retry yet
    assert b.estimate("openrouter:openai/gpt-6-sol", 1000, 100) is None
    assert calls == [1]

    clock["now"] += 2  # cool-down elapsed: retries, and this time the fetch succeeds
    assert b.estimate("openrouter:openai/gpt-6-sol", 1000, 100) == pytest.approx(0.003)
    assert calls == [1, 2]

    # Prices are now loaded for good; no further retries are needed.
    assert b.estimate("openrouter:openai/gpt-6-sol", 1000, 100) == pytest.approx(0.003)
    assert calls == [1, 2]


def test_negative_prices_are_skipped(tmp_path):
    # OpenRouter lists router models (e.g. openrouter/auto) with "-1" prices: a sentinel, not a
    # refund per token.
    data = {"data": [
        {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
        {"id": "x/half", "pricing": {"prompt": "0.000001", "completion": "-1"}},
        {"id": "x/free", "pricing": {"prompt": "0", "completion": "0"}},
    ]}
    b = book(tmp_path, fetch=lambda: data)
    assert b.price("openrouter:openrouter/auto") is None
    assert b.price("openrouter:x/half") is None
    assert b.price("openrouter:x/free") == Price(prompt=0.0, completion=0.0)


@pytest.mark.parametrize(
    "bad",
    [[], "nope", {"data": "nope"}, {"data": {"id": "x"}}, {"data": [1, "two"]}, {"nodata": []}],
)
def test_a_malformed_fetch_is_no_data_and_not_cached(tmp_path, bad):
    b = book(tmp_path, fetch=lambda: bad)
    assert b.price("openrouter:openai/gpt-6-sol") is None
    assert not (tmp_path / "models.json").exists()


def test_a_malformed_fetch_falls_back_to_the_stale_cache(tmp_path):
    book(tmp_path).price("openrouter:openai/gpt-6-sol")  # writes the cache
    later = book(tmp_path, fetch=lambda: {"data": "nope"}, now=1_000_000.0 + 2 * 86400)
    assert later.price("openrouter:openai/gpt-6-sol") is not None


def test_a_malformed_cache_is_ignored(tmp_path):
    (tmp_path / "models.json").write_text(json.dumps({"fetched_at": 1_000_000.0, "data": {"data": "nope"}}))
    assert book(tmp_path).price("openrouter:openai/gpt-6-sol") is not None  # refetched instead


def test_the_default_fetch_caps_the_response_size(monkeypatch):
    from phil.agents import pricing

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body
            self.asked: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.asked.append(size)
            return self.body if size < 0 else self.body[:size]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    big = Response(b" " * (pricing.MAX_RESPONSE_BYTES + 10))
    monkeypatch.setattr(pricing.urllib.request, "urlopen", lambda url, timeout: big)
    with pytest.raises(ValueError, match="too large"):
        real_default_fetch()
    assert big.asked == [pricing.MAX_RESPONSE_BYTES + 1]

    small = Response(b'{"data": []}')
    monkeypatch.setattr(pricing.urllib.request, "urlopen", lambda url, timeout: small)
    assert real_default_fetch() == {"data": []}
    assert pricing.MAX_RESPONSE_BYTES == 20 * 1024 * 1024
