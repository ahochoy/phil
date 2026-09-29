import pytest

from tests.conftest import guard_against_price_fetches


def test_network_guard_is_quiet_when_no_fetch_is_attempted(monkeypatch):
    gen = guard_against_price_fetches(monkeypatch)
    next(gen)  # run up to yield
    with pytest.raises(StopIteration):
        next(gen)  # no fetch happened; the generator just finishes


def test_network_guard_fails_loudly_when_a_fetch_is_attempted(monkeypatch):
    # PriceBook._load() swallows any exception a fetch raises, so the guard can't rely on the
    # AssertionError alone to fail the test — it must also fail on its own, after `yield`.
    from phil.agents import pricing

    gen = guard_against_price_fetches(monkeypatch)
    next(gen)  # run up to yield; installs the refusing fetch
    with pytest.raises(AssertionError):
        pricing._default_fetch()
    with pytest.raises(pytest.fail.Exception):
        next(gen)  # back in control after yield: the attempted fetch must fail the test
