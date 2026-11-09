import pytest

from phil.agents.pricing import PriceBook
from phil.agents.unpriced import (
    PLANNING_ROLES, Unpriced, planning_notice, price_label, run_notice, unpriced_models, usd,
)
from phil.config import RUN_ROLES, PhilConfig

LIST = {
    "data": [
        {"id": "anthropic/claude-sonnet-5.5", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
        {"id": "openai/gpt-6-sol", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
    ]
}


def book(tmp_path, data=LIST):
    return PriceBook(tmp_path / "prices.json", fetch=lambda: data, clock=lambda: 1_000_000.0)


def no_fetch(tmp_path):
    def boom():
        raise AssertionError("the price list should not have been consulted")

    return PriceBook(tmp_path / "unused.json", fetch=boom, clock=lambda: 1_000_000.0)


def config(models, providers=None):
    return PhilConfig.model_validate({"models": models, "providers": providers or {}})


def test_a_direct_model_the_list_has_is_priced(tmp_path):
    cfg = config({"high": "anthropic:claude-sonnet-5-5", "low": "anthropic:claude-sonnet-5-5"})
    assert unpriced_models(cfg, RUN_ROLES, book(tmp_path)) == []


def test_a_direct_model_the_list_lacks_is_unpriced_with_its_roles(tmp_path):
    cfg = config({"high": "anthropic:claude-x", "low": "openai:gpt-6-sol"})
    assert unpriced_models(cfg, ("architect", "implementer", "critic"), book(tmp_path)) == [
        Unpriced("anthropic:claude-x", "anthropic", ("architect", "critic")),
    ]


def test_openrouter_ollama_and_configured_prices_are_never_unpriced(tmp_path):
    cfg = config(
        {"high": "openrouter:vendor/not-listed", "low": "ollama:qwen3:8b", "tester": "lab:llama"},
        {"lab": {"kind": "openai", "base_url": "http://localhost:8000/v1", "input_per_mtok": 1.0, "output_per_mtok": 2.0}},
    )
    assert unpriced_models(cfg, ("architect", "implementer", "tester"), no_fetch(tmp_path)) == []


def test_a_local_server_without_prices_is_unpriced_without_a_lookup(tmp_path):
    cfg = config({"high": "ollama:x", "low": "lab:llama"}, {"lab": {"kind": "openai", "base_url": "http://localhost:8000/v1"}})
    assert unpriced_models(cfg, ("implementer",), no_fetch(tmp_path)) == [Unpriced("lab:llama", "lab", ("implementer",))]


def test_an_anthropic_proxy_is_unpriced_and_named(tmp_path):
    cfg = config({"high": "anthropic:claude-sonnet-5-5", "low": "anthropic:claude-sonnet-5-5"},
                 {"anthropic": {"base_url": "https://proxy.example.com"}})
    items = unpriced_models(cfg, ("implementer",), no_fetch(tmp_path))
    assert items == [Unpriced("anthropic:claude-sonnet-5-5", "anthropic", ("implementer",))]
    assert "`[providers.anthropic]`" in run_notice(items, max_cost_usd=2.0, max_tokens=1_500_000)


def test_an_anthropic_proxy_set_in_the_environment_is_unpriced(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
    cfg = config({"high": "anthropic:claude-sonnet-5-5", "low": "anthropic:claude-sonnet-5-5"})
    assert unpriced_models(cfg, ("implementer",), no_fetch(tmp_path)) == [
        Unpriced("anthropic:claude-sonnet-5-5", "anthropic", ("implementer",))
    ]


def offline(tmp_path):
    def down():
        raise OSError("offline")

    return PriceBook(tmp_path / "offline.json", fetch=down, clock=lambda: 1_000_000.0)


def test_a_price_list_that_cant_be_fetched_is_named_not_blamed_on_the_config(tmp_path):
    cfg = config({"high": "anthropic:claude-sonnet-5-5", "low": "anthropic:claude-sonnet-5-5"})
    items = unpriced_models(cfg, ("implementer",), offline(tmp_path))
    assert items == [Unpriced("anthropic:claude-sonnet-5-5", "anthropic", ("implementer",), listless=True)]
    assert run_notice(items, max_cost_usd=2.0, max_tokens=1_500_000) == (
        "Couldn't fetch OpenRouter's price list, so the $2.00 run budget can't count "
        "`anthropic:claude-sonnet-5-5` (implementer). The run still stops at 1,500,000 tokens."
    )
    assert planning_notice(items, max_cost_usd=1.0) == (
        "Couldn't fetch OpenRouter's price list, so this goal's $1.00 planning budget (chat.max_cost_usd) "
        "can't count `anthropic:claude-sonnet-5-5` (implementer)."
    )


def test_an_unfetched_list_and_a_priceless_provider_get_a_sentence_each(tmp_path):
    items = [
        Unpriced("lab:llama", "lab", ("tester",)),
        Unpriced("anthropic:claude-sonnet-5-5", "anthropic", ("implementer",), listless=True),
    ]
    assert run_notice(items, max_cost_usd=2.0, max_tokens=100) == (
        "No price for `lab:llama` (tester), so the $2.00 run budget can't count it. "
        "Couldn't fetch OpenRouter's price list, so it can't count `anthropic:claude-sonnet-5-5` (implementer) either. "
        "The run still stops at 100 tokens. "
        "To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.lab]`."
    )


def test_roles_without_a_model_or_with_an_unknown_provider_are_skipped(tmp_path):
    cfg = config({"low": "nowhere:model"})
    assert unpriced_models(cfg, ("architect", "implementer"), no_fetch(tmp_path)) == []


def test_the_run_notice_for_one_model():
    items = [Unpriced("anthropic:claude-x", "anthropic", ("architect", "tester"))]
    assert run_notice(items, max_cost_usd=2.0, max_tokens=1_500_000) == (
        "No price for `anthropic:claude-x` (architect, tester), so the $2.00 run budget can't count it. "
        "The run still stops at 1,500,000 tokens. "
        "To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.anthropic]`."
    )


def test_the_run_notice_for_several_models_names_each_provider_once():
    items = [
        Unpriced("anthropic:claude-x", "anthropic", ("implementer",)),
        Unpriced("anthropic:claude-y", "anthropic", ("tester",)),
        Unpriced("lab:llama", "lab", ("reviewer",)),
    ]
    assert run_notice(items, max_cost_usd=5.0, max_tokens=100_000) == (
        "No price for `anthropic:claude-x` (implementer), `anthropic:claude-y` (tester), `lab:llama` (reviewer), "
        "so the $5.00 run budget can't count them. The run still stops at 100,000 tokens. "
        "To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.anthropic]` and `[providers.lab]`."
    )


def test_the_planning_notice_names_the_planning_budget():
    items = [Unpriced("lab:llama", "lab", ("architect",))]
    assert planning_notice(items, max_cost_usd=1.0) == (
        "No price for `lab:llama` (architect), so this goal's $1.00 planning budget (chat.max_cost_usd) "
        "can't count it. To fix it, set `input_per_mtok` and `output_per_mtok` under `[providers.lab]`."
    )


def test_no_unpriced_models_means_no_notice():
    assert run_notice([], max_cost_usd=2.0, max_tokens=1) is None
    assert planning_notice([], max_cost_usd=1.0) is None


def test_planning_roles_are_the_goal_pipelines():
    assert PLANNING_ROLES == ("classifier", "orchestrator", "designer", "architect", "critic")


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("anthropic:claude-sonnet-5-5", "$2.00/$10.00 per M"),
        ("anthropic:claude-x", "no price"),
        ("ollama:qwen3:8b", "$0.00/$0.00 per M"),
        ("openrouter:openai/gpt-6-sol", "$2.00/$10.00 per M"),
        ("openrouter:vendor/not-listed", "price reported per call"),
        ("typesafe:jev", None),
        ("nowhere:model", None),
    ],
)
def test_price_label(tmp_path, model, expected):
    key = "classifier" if model.startswith("typesafe:") else "low"  # a decision model can only classify
    assert price_label(config({key: model}), model, book(tmp_path)) == expected


def test_usd_shows_small_prices_to_three_places():
    assert (usd(0.0), usd(0.1), usd(2.0), usd(0.042)) == ("$0.00", "$0.10", "$2.00", "$0.042")
