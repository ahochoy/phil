import json
from pathlib import Path

import pytest

from phil.agents.pricing import Price, PriceBook

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
