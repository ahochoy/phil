"""Can a model read images? Answered from OpenRouter's model list (spec 2026-11-19 images, "Reaching a
model that can see")."""

from phil.agents.pricing import PriceBook, book_ids
from phil.agents.providers import ProviderSpec, split_model


def sees_images(model: str, provider: ProviderSpec | None, book: PriceBook) -> bool | None:
    """True or False from the list's `input_modalities` for `model` (`provider:name`, on `provider`;
    None when it has no spec and is `openrouter:<id>`); None when unknown: a provider the list doesn't
    cover (a local server, a proxy), a model it doesn't list, or an entry that gives no input modalities."""
    prefix, name = split_model(model)
    kind = provider.kind if provider is not None else prefix
    base_url = provider.base_url if provider is not None else None
    modalities = book.modalities(book_ids(kind, base_url, name))
    return None if modalities is None else "image" in modalities
