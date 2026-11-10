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
    listless: bool = False  # the list would price it, but couldn't be fetched (nothing to fix in the config)


def usd(value: float) -> str:
    return f"${value:.2f}" if value == 0 or value >= 0.1 else f"${value:.3f}"


def _configured(provider: ProviderSpec) -> bool:
    return provider.input_per_mtok is not None and provider.output_per_mtok is not None


def _book_ids(provider: ProviderSpec, model: str) -> list[str]:
    return book_ids(provider.kind, provider.base_url, split_model(model)[1])


def _book_price(provider: ProviderSpec, model: str, book: PriceBook) -> Price | None:
    return book.find(_book_ids(provider, model))


def _status(provider: ProviderSpec, model: str, book: PriceBook) -> str:
    """"priced", "unpriced", or "listless": the list would be asked but couldn't be fetched."""
    # OpenRouter reports each call's cost; decision models (Jev) keep their own price.
    if provider.kind in ("openrouter", SYSTEMONE) or _configured(provider):
        return "priced"
    ids = _book_ids(provider, model)
    if book.find(ids) is not None:
        return "priced"
    return "listless" if ids and not book.available() else "unpriced"


def unpriced_models(config: PhilConfig, roles: Sequence[str], book: PriceBook) -> list[Unpriced]:
    """Each model `roles` resolve to that Phil can't price, with the roles using it, in role order.
    A role with no model or an unknown provider is skipped: other checks report those."""
    found: dict[str, tuple[str, list[str]]] = {}
    status: dict[str, str] = {}
    for role in roles:
        try:
            model = config.model_for(role)
            provider = provider_for_model(config, model, config.model_owner(role))
            if model not in status:
                status[model] = _status(provider, model, book)
        except Exception:
            continue
        if status[model] != "priced":
            found.setdefault(model, (provider.name, []))[1].append(role)
    return [
        Unpriced(model, provider, tuple(users), listless=status[model] == "listless")
        for model, (provider, users) in found.items()
    ]


def _models(items: Sequence[Unpriced]) -> str:
    return ", ".join(f"`{item.model}` ({', '.join(item.roles)})" for item in items)


def _fix(items: Sequence[Unpriced]) -> str:
    sections = " and ".join(f"`[providers.{name}]`" for name in dict.fromkeys(item.provider for item in items))
    return f"To fix it, set `input_per_mtok` and `output_per_mtok` under {sections}."


def _them(items: Sequence[Unpriced]) -> str:
    return "it" if len(items) == 1 else "them"


def _notice(items: Sequence[Unpriced], budget: str, limit: str | None) -> str | None:
    """One line: the models with no price and the fix, and those the list couldn't be fetched for
    (nothing to fix in the config: Phil tries the list again later)."""
    if not items:
        return None
    priceless = [item for item in items if not item.listless]
    unfetched = [item for item in items if item.listless]
    parts: list[str] = []
    if priceless:
        parts.append(f"No price for {_models(priceless)}, so {budget} can't count {_them(priceless)}.")
    if unfetched:
        if priceless:
            parts.append(f"Couldn't fetch OpenRouter's price list, so it can't count {_models(unfetched)} either.")
        else:
            parts.append(f"Couldn't fetch OpenRouter's price list, so {budget} can't count {_models(unfetched)}.")
    if limit is not None:
        parts.append(limit)
    if priceless:
        parts.append(_fix(priceless))
    return " ".join(parts)


def run_notice(items: Sequence[Unpriced], *, max_cost_usd: float, max_tokens: int) -> str | None:
    return _notice(items, f"the ${max_cost_usd:.2f} run budget", f"The run still stops at {max_tokens:,} tokens.")


def planning_notice(items: Sequence[Unpriced], *, max_cost_usd: float) -> str | None:
    return _notice(items, f"this goal's ${max_cost_usd:.2f} planning budget (chat.max_cost_usd)", None)


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
