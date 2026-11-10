import contextlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_MAX_AGE_S = 24 * 3600
RETRY_AFTER_FAILURE_S = 10 * 60  # cool-down before retrying a fetch that failed with no cache to fall back on
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # the model list is a few MB; refuse anything absurd


@dataclass(frozen=True)
class Tier:
    """A long-context price: it applies to a call whose input reaches `min_prompt_tokens`."""

    min_prompt_tokens: int
    prompt: float
    completion: float
    cache_read: float | None = None
    cache_write: float | None = None


@dataclass(frozen=True)
class Price:
    prompt: float  # USD per token
    completion: float
    cache_read: float | None = None  # None: the list has no cache price; the prompt price is used
    cache_write: float | None = None
    tiers: tuple[Tier, ...] = ()  # ascending by min_prompt_tokens

    def cost(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
        """One call's cost (spec 2026-11-09 §4.5). Cached tokens are part of `input_tokens`."""
        rates: Price | Tier = self
        for tier in self.tiers:
            if input_tokens >= tier.min_prompt_tokens:
                rates = tier  # ascending, so the last one reached is the highest threshold
        read = rates.prompt if rates.cache_read is None else rates.cache_read
        write = rates.prompt if rates.cache_write is None else rates.cache_write
        uncached = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
        return (
            uncached * rates.prompt
            + cache_read_tokens * read
            + cache_write_tokens * write
            + output_tokens * rates.completion
        )


# The vendors whose own APIs the list prices, under `<kind>/<model>` ids.
DIRECT_KINDS = ("anthropic", "openai", "google")
_DATE_SUFFIX = re.compile(r"-\d{8}$")
_DIGIT_DASH = re.compile(r"(?<=\d)-(?=\d)")


# Where each vendor's SDK sends a built-in provider's calls when Phil passes no base_url.
BASE_URL_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_URL"),
    "openai": ("OPENAI_BASE_URL", "OPENAI_API_BASE"),
    "google": ("GOOGLE_GEMINI_BASE_URL",),
}


def _vendor_endpoint(kind: str, base_url: str | None, environ: Mapping[str, str]) -> bool:
    """The vendor's own API: no base_url (nor one in the SDK's environment variables), or OpenAI's
    own host. Anything else (a proxy, a local server) may charge anything, so the list's price
    would be a guess."""
    if base_url is None:
        base_url = next((environ[name] for name in BASE_URL_ENV.get(kind, ()) if environ.get(name)), None)
    if base_url is None:
        return True
    return kind == "openai" and urlsplit(base_url).hostname == "api.openai.com"


def book_ids(kind: str, base_url: str | None, name: str, environ: Mapping[str, str] | None = None) -> list[str]:
    """The OpenRouter ids to price a `kind` provider's model `name` by, most exact first (spec
    2026-11-09 §4.2); [] when the list doesn't price that provider. `environ` (the process
    environment by default) is where the vendor SDKs look for a base URL Phil didn't set."""
    if kind == "openrouter":
        return [name]
    environ = os.environ if environ is None else environ
    if kind not in DIRECT_KINDS or not _vendor_endpoint(kind, base_url, environ) or name.endswith(":batch"):
        return []
    ids: list[str] = []
    for candidate in (name, _DIGIT_DASH.sub(".", name), _DIGIT_DASH.sub(".", _DATE_SUFFIX.sub("", name))):
        model_id = f"{kind}/{candidate}"
        if model_id not in ids:
            ids.append(model_id)
    return ids


def _rate(pricing: dict, key: str) -> float | None:
    """A non-negative USD-per-token price, or None when missing, malformed or a router's "-1"."""
    if key not in pricing:
        return None
    try:
        value = float(pricing[key])
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _tiers(pricing: dict, base: Price) -> tuple[Tier, ...]:
    tiers: list[Tier] = []
    for override in pricing.get("overrides") or []:
        if not isinstance(override, dict):
            continue
        try:
            threshold = int(override["min_prompt_tokens"])
        except (KeyError, TypeError, ValueError):
            continue
        prompt, completion = _rate(override, "prompt"), _rate(override, "completion")
        read, write = _rate(override, "input_cache_read"), _rate(override, "input_cache_write")
        tiers.append(
            Tier(
                threshold,
                prompt=base.prompt if prompt is None else prompt,
                completion=base.completion if completion is None else completion,
                cache_read=base.cache_read if read is None else read,
                cache_write=base.cache_write if write is None else write,
            )
        )
    return tuple(sorted(tiers, key=lambda tier: tier.min_prompt_tokens))


def _default_fetch() -> dict:
    with urllib.request.urlopen(MODELS_URL, timeout=10) as response:  # noqa: S310 - fixed, known host
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"OpenRouter model list too large (over {MAX_RESPONSE_BYTES} bytes)")
    return json.loads(body)


def _well_formed(data: object) -> bool:
    """The model-list shape the parser relies on: ``{"data": [{...}, ...]}``. Anything else (an
    error page, a changed API) counts as no data: never cached, never parsed."""
    return (
        isinstance(data, dict)
        and isinstance(data.get("data"), list)
        and all(isinstance(entry, dict) for entry in data["data"])
    )


class PriceBook:
    def __init__(
        self,
        cache_path: Path,
        *,
        fetch: Callable[[], dict] | None = None,
        clock: Callable[[], float] = time.time,
        max_age_s: float = CACHE_MAX_AGE_S,
        retry_after_failure_s: float = RETRY_AFTER_FAILURE_S,
    ) -> None:
        self._cache_path = cache_path
        self._fetch = fetch if fetch is not None else _default_fetch
        self._clock = clock
        self._max_age_s = max_age_s
        self._retry_after_failure_s = retry_after_failure_s
        self._lock = threading.Lock()
        self._loaded = False
        self._failed_at: float | None = None
        self._prices: dict[str, Price] = {}
        self._catalog: list[tuple[str, float | None, float | None]] = []

    def _write_cache_atomically(self, data: dict) -> None:
        # Fetched-at time comes from our own (possibly injected) clock, not the file's mtime,
        # so freshness checks stay consistent when tests fake the clock.
        envelope = {"fetched_at": self._clock(), "data": data}
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(self._cache_path.parent), prefix=".pricebook-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(envelope, handle)
            os.replace(tmp_path, self._cache_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def _read_cache_envelope(self) -> dict | None:
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _read_fresh_cache(self) -> dict | None:
        envelope = self._read_cache_envelope()
        if envelope is None:
            return None
        fetched_at = envelope.get("fetched_at", 0) if isinstance(envelope, dict) else 0
        if not isinstance(fetched_at, (int, float)) or self._clock() - fetched_at >= self._max_age_s:
            return None
        data = envelope.get("data")
        return data if _well_formed(data) else None

    def _read_stale_cache(self) -> dict | None:
        envelope = self._read_cache_envelope()
        data = envelope.get("data") if isinstance(envelope, dict) else None
        return data if _well_formed(data) else None

    def _parse(self, data: dict) -> dict[str, Price]:
        prices: dict[str, Price] = {}
        for entry in data.get("data", []):
            model_id = entry.get("id")
            pricing = entry.get("pricing") or {}
            if not model_id or not isinstance(pricing, dict):
                continue
            prompt, completion = _rate(pricing, "prompt"), _rate(pricing, "completion")
            if prompt is None or completion is None:
                continue  # missing, malformed, or OpenRouter's "-1" sentinel for router models (e.g. openrouter/auto)
            base = Price(prompt, completion, _rate(pricing, "input_cache_read"), _rate(pricing, "input_cache_write"))
            prices[model_id] = replace(base, tiers=_tiers(pricing, base))
        return prices

    def _parse_catalog(self, data: dict) -> list[tuple[str, float | None, float | None]]:
        """Every model in the list, in its order: (id, input, output), prices in USD per million
        tokens, or None where the entry has no usable price (missing, malformed or a router's "-1")."""
        catalog: list[tuple[str, float | None, float | None]] = []
        for entry in data.get("data", []):
            model_id = entry.get("id")
            if not model_id or not isinstance(model_id, str):
                continue
            price = self._prices.get(model_id)
            if price is None:
                catalog.append((model_id, None, None))
            else:
                catalog.append((model_id, price.prompt * 1e6, price.completion * 1e6))
        return catalog

    def _load(self) -> None:
        if self._loaded:
            return
        if self._failed_at is not None and self._clock() - self._failed_at < self._retry_after_failure_s:
            return  # cooling down after a fetch failure with no cache to fall back on; stay empty
        data = self._read_fresh_cache()
        if data is None:
            try:
                fetched = self._fetch()
            except Exception:
                fetched = None
            if not _well_formed(fetched):
                fetched = None
            if fetched is not None:
                data = fetched
                try:
                    self._write_cache_atomically(fetched)
                except OSError:
                    pass
            else:
                data = self._read_stale_cache()  # fall back to a stale cache if present
        if data is None:
            # Nothing to show for this attempt (no cache, fresh or stale, and no fetch): leave
            # `_loaded` False so a later call retries once the cool-down above has elapsed,
            # instead of caching "no prices" for the rest of the process.
            self._failed_at = self._clock()
            self._prices = {}
            return
        self._prices = self._parse(data)
        self._catalog = self._parse_catalog(data)
        self._loaded = True

    def find(self, ids: Sequence[str]) -> Price | None:
        """The price of the first of `ids` (OpenRouter ids, no `openrouter:` prefix) the list has;
        None when it has none of them or is unavailable. No ids: None, without loading the list."""
        if not ids:
            return None
        try:
            with self._lock:
                self._load()
            for model_id in ids:
                price = self._prices.get(model_id)
                if price is not None:
                    return price
            return None
        except Exception:
            return None

    def available(self) -> bool:
        """Whether the list could be loaded (fetched, or from a cache, fresh or stale)."""
        try:
            with self._lock:
                self._load()
                return self._loaded
        except Exception:
            return False

    def price(self, model: str) -> Price | None:
        if not model.startswith("openrouter:"):
            return None
        return self.find([model.removeprefix("openrouter:")])

    def models(self) -> list[tuple[str, float | None, float | None]]:
        """The OpenRouter catalog, loading the book if needed: (id, input, output) per model in the
        list's order, prices in USD per million tokens (None when unpriced). Empty when unavailable."""
        try:
            with self._lock:
                self._load()
                return list(self._catalog)
        except Exception:
            return []

    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        try:
            price = self.price(model)
            return None if price is None else price.cost(input_tokens, output_tokens)
        except Exception:
            return None


def default_price_book() -> PriceBook:
    from phil.store.paths import phil_home

    return PriceBook(phil_home() / "cache" / "openrouter-models.json")
