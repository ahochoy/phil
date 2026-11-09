"""The configured models Phil can't price, the line that says so before work starts, and the price
`phil models check` shows (spec 2026-11-09 §4.2, §4.6)."""

from collections.abc import Sequence
from dataclasses import dataclass

from phil.agents.pricing import Price, PriceBook, book_ids
from phil.agents.providers import SYSTEMONE, ProviderSpec, provider_for_model, split_model
from phil.config import PhilConfig

# The chat's agents for one goal: routing, intake, design, the architect and the critic.
PLANNING_ROLES = ("classifier", "orchestrator", "designer", "architect", "critic")


@dataclass(frozen=True)
class Unpriced:
    model: str
    provider: str  # the [providers.<name>] that would fix it
    roles: tuple[str, ...]


def usd(value: float) -> str:
    return f"${value:.2f}" if value == 0 or value >= 0.1 else f"${value:.3f}"


def _configured(provider: ProviderSpec) -> bool:
    return provider.input_per_mtok is not None and provider.output_per_mtok is not None


def _book_price(provider: ProviderSpec, model: str, book: PriceBook) -> Price | None:
    return book.find(book_ids(provider.kind, provider.base_url, split_model(model)[1]))


def _priced(provider: ProviderSpec, model: str, book: PriceBook) -> bool:
    # OpenRouter reports each call's cost; decision models (Jev) keep their own price.
    if provider.kind in ("openrouter", SYSTEMONE) or _configured(provider):
        return True
    return _book_price(provider, model, book) is not None


def unpriced_models(config: PhilConfig, roles: Sequence[str], book: PriceBook) -> list[Unpriced]:
    """Each model `roles` resolve to that Phil can't price, with the roles using it, in role order.
    A role with no model or an unknown provider is skipped: other checks report those."""
    found: dict[str, tuple[str, list[str]]] = {}
    priced: dict[str, bool] = {}
    for role in roles:
        try:
            model = config.model_for(role)
            provider = provider_for_model(config, model, config.model_owner(role))
            if model not in priced:
                priced[model] = _priced(provider, model, book)
        except Exception:
            continue
        if not priced[model]:
            found.setdefault(model, (provider.name, []))[1].append(role)
    return [Unpriced(model, provider, tuple(users)) for model, (provider, users) in found.items()]


def _models(items: Sequence[Unpriced]) -> str:
    return ", ".join(f"`{item.model}` ({', '.join(item.roles)})" for item in items)


def _fix(items: Sequence[Unpriced]) -> str:
    sections = " and ".join(f"`[providers.{name}]`" for name in dict.fromkeys(item.provider for item in items))
    return f"To fix it, set `input_per_mtok` and `output_per_mtok` under {sections}."


def _them(items: Sequence[Unpriced]) -> str:
    return "it" if len(items) == 1 else "them"


def run_notice(items: Sequence[Unpriced], *, max_cost_usd: float, max_tokens: int) -> str | None:
    if not items:
        return None
    return (
        f"No price for {_models(items)}, so the ${max_cost_usd:.2f} run budget can't count {_them(items)}. "
        f"The run still stops at {max_tokens:,} tokens. {_fix(items)}"
    )


def planning_notice(items: Sequence[Unpriced], *, max_cost_usd: float) -> str | None:
    if not items:
        return None
    return (
        f"No price for {_models(items)}, so this goal's ${max_cost_usd:.2f} planning budget "
        f"(chat.max_cost_usd) can't count {_them(items)}. {_fix(items)}"
    )


def price_label(config: PhilConfig, model: str, book: PriceBook) -> str | None:
    """`$in/$out per M` for `model`, "price reported per call" for an OpenRouter model the list
    lacks, "no price" otherwise; None for a decision model or a model Phil can't resolve."""
    try:
        provider = provider_for_model(config, model, model)
        if provider.kind == SYSTEMONE:
            return None
        if _configured(provider):
            return f"{usd(provider.input_per_mtok)}/{usd(provider.output_per_mtok)} per M"
        price = _book_price(provider, model, book)
    except Exception:  # an unknown provider or a bad config: no label
        return None
    if price is not None:
        return f"{usd(price.prompt * 1e6)}/{usd(price.completion * 1e6)} per M"
    return "price reported per call" if provider.kind == "openrouter" else "no price"
