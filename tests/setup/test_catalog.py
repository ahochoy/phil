import json
from pathlib import Path

import httpx
import pytest

from phil.agents.pricing import PriceBook
from phil.setup.catalog import CatalogModel, ollama_models, openrouter_catalog, search_openrouter

FIXTURE = json.loads((Path(__file__).parents[1] / "agents" / "fixtures" / "openrouter-models.json").read_text())

CATALOG = [
    CatalogModel("openai/gpt-6-sol", 2.0, 10.0),
    CatalogModel("openai/gpt-6-luna", 0.1, 0.5),
    CatalogModel("anthropic/claude-sonnet-5", 3.0, 15.0),
    CatalogModel("google/gemini-3.8-flash", 0.3, 2.5),
    CatalogModel("openai/gpt-6-terra", 0.5, 4.0),
]


def test_search_is_a_case_insensitive_substring_match_in_catalog_order():
    assert [m.id for m in search_openrouter("GPT-6", CATALOG)] == [
        "openai/gpt-6-sol",
        "openai/gpt-6-luna",
        "openai/gpt-6-terra",
    ]
    assert [m.id for m in search_openrouter("flash", CATALOG)] == ["google/gemini-3.8-flash"]
    assert search_openrouter("nothing-like-this", CATALOG) == []


def test_search_stops_at_the_limit():
    many = [CatalogModel(f"vendor/model-{n}", None, None) for n in range(30)]
    found = search_openrouter("model", many)
    assert [m.id for m in found] == [f"vendor/model-{n}" for n in range(10)]
    assert len(search_openrouter("model", many, limit=3)) == 3


def test_price_book_models_lists_the_catalog_with_prices_per_million_tokens(tmp_path):
    data = {
        "data": [
            *FIXTURE["data"],
            {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
            {"id": "vendor/no-pricing"},
            {"pricing": {"prompt": "0", "completion": "0"}},  # no id: skipped
        ]
    }
    book = PriceBook(tmp_path / "models.json", fetch=lambda: data, clock=lambda: 1_000_000.0)
    models = book.models()
    assert [m[0] for m in models] == [
        "openai/gpt-6-sol",
        "openai/gpt-6-luna",
        "anthropic/claude-flash",
        "openrouter/auto",
        "vendor/no-pricing",
    ]
    assert models[0] == ("openai/gpt-6-sol", pytest.approx(2.0), pytest.approx(10.0))
    assert models[3] == ("openrouter/auto", None, None)
    assert models[4] == ("vendor/no-pricing", None, None)


def test_openrouter_catalog_wraps_the_price_book(tmp_path):
    book = PriceBook(tmp_path / "models.json", fetch=lambda: FIXTURE, clock=lambda: 1_000_000.0)
    catalog = openrouter_catalog(book)
    assert catalog[0] == CatalogModel("openai/gpt-6-sol", pytest.approx(2.0), pytest.approx(10.0))
    assert [m.id for m in catalog] == ["openai/gpt-6-sol", "openai/gpt-6-luna", "anthropic/claude-flash"]


def test_openrouter_catalog_is_empty_when_the_list_is_unavailable(tmp_path):
    def offline():
        raise OSError("offline")

    book = PriceBook(tmp_path / "models.json", fetch=offline, clock=lambda: 1_000_000.0)
    assert openrouter_catalog(book) == []


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", "http://x"), response=None)

    def json(self):
        return self._payload


def test_ollama_models_lists_the_installed_models():
    calls = []

    def get(url, timeout):
        calls.append((url, timeout))
        return FakeResponse({"models": [{"name": "qwen3:32b"}, {"name": "llama4:8b"}]})

    assert ollama_models("http://localhost:11434/v1", get=get) == ["qwen3:32b", "llama4:8b"]
    assert calls == [("http://localhost:11434/api/tags", 3.0)]


def test_ollama_models_handles_a_base_url_without_v1():
    urls = []

    def get(url, timeout):
        urls.append(url)
        return FakeResponse({"models": []})

    assert ollama_models("http://box:11434/", get=get) == []
    assert urls == ["http://box:11434/api/tags"]


@pytest.mark.parametrize(
    "get",
    [
        lambda url, timeout: (_ for _ in ()).throw(httpx.ConnectError("refused")),
        lambda url, timeout: FakeResponse({}, status=500),
        lambda url, timeout: FakeResponse({"unexpected": True}),
        lambda url, timeout: FakeResponse({"models": [{"no_name": 1}]}),
    ],
)
def test_ollama_models_is_none_on_any_error(get):
    assert ollama_models("http://localhost:11434/v1", get=get) is None
