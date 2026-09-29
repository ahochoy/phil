import json
from pathlib import Path

import pytest

from phil.agents.pricing import RETRY_AFTER_FAILURE_S, Price, PriceBook

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
