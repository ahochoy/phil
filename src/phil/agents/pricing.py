import contextlib
import json
import os
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_MAX_AGE_S = 24 * 3600
RETRY_AFTER_FAILURE_S = 10 * 60  # cool-down before retrying a fetch that failed with no cache to fall back on
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # the model list is a few MB; refuse anything absurd


@dataclass(frozen=True)
class Price:
    prompt: float  # USD per token
    completion: float


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
            with os.fdopen(fd, "w") as handle:
                json.dump(envelope, handle)
            os.replace(tmp_path, self._cache_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def _read_cache_envelope(self) -> dict | None:
        try:
            return json.loads(self._cache_path.read_text())
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
            if not model_id or "prompt" not in pricing or "completion" not in pricing:
                continue
            try:
                price = Price(prompt=float(pricing["prompt"]), completion=float(pricing["completion"]))
            except (TypeError, ValueError):
                continue
            if price.prompt < 0 or price.completion < 0:
                continue  # OpenRouter's "-1" sentinel for router models (e.g. openrouter/auto)
            prices[model_id] = price
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

    def price(self, model: str) -> Price | None:
        try:
            if not model.startswith("openrouter:"):
                return None
            with self._lock:
                self._load()
            model_id = model.removeprefix("openrouter:")
            return self._prices.get(model_id)
        except Exception:
            return None

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
            if price is None:
                return None
            return price.prompt * input_tokens + price.completion * output_tokens
        except Exception:
            return None


def default_price_book() -> PriceBook:
    from phil.store.paths import phil_home

    return PriceBook(phil_home() / "cache" / "openrouter-models.json")
