"""Where setup finds models to offer: OpenRouter's cached catalog, and a local Ollama's tags."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from phil.agents.pricing import PriceBook


@dataclass(frozen=True)
class CatalogModel:
    id: str  # OpenRouter's id, without the `openrouter:` prefix
    input_per_mtok: float | None  # USD per million tokens; None when unpriced
    output_per_mtok: float | None


def search_openrouter(query: str, catalog: list[CatalogModel], limit: int = 10) -> list[CatalogModel]:
    """Up to `limit` models whose id contains `query` (ignoring case), in catalog order."""
    needle = query.strip().lower()
    found: list[CatalogModel] = []
    for model in catalog:
        if needle in model.id.lower():
            found.append(model)
            if len(found) >= limit:
                break
    return found


def openrouter_catalog(book: "PriceBook | None" = None) -> list[CatalogModel]:
    """OpenRouter's model list from the price book's cache (fetched when stale). Empty when unavailable."""
    if book is None:
        from phil.agents.pricing import default_price_book

        book = default_price_book()
    return [CatalogModel(model_id, prompt, completion) for model_id, prompt, completion in book.models()]


@dataclass(frozen=True)
class OllamaModel:
    name: str
    tools: bool | None  # can it call tools; None when this Ollama doesn't report capabilities


def ollama_models(
    base_url: str, *, get: Callable[..., Any] = httpx.get, timeout: float = 3.0
) -> list[OllamaModel] | None:
    """The models installed in the Ollama at `base_url` (its OpenAI-compatible `/v1` URL or its
    root), from `GET /api/tags`, with whether each can call tools. None when it can't be reached or
    answers unexpectedly."""
    root = base_url.rstrip("/").removesuffix("/v1").rstrip("/")
    try:
        response = get(f"{root}/api/tags", timeout=timeout)
        response.raise_for_status()
        models = []
        for model in response.json()["models"]:
            capabilities = model.get("capabilities")
            tools = None if capabilities is None else "tools" in capabilities
            models.append(OllamaModel(str(model["name"]), tools))
        return models
    except Exception:
        return None
