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


@dataclass(frozen=True)
class Price:
    prompt: float  # USD per token
    completion: float


def _default_fetch() -> dict:
    with urllib.request.urlopen(MODELS_URL, timeout=10) as response:  # noqa: S310 - fixed, known host
        return json.loads(response.read())


class PriceBook:
    def __init__(
        self,
        cache_path: Path,
        *,
        fetch: Callable[[], dict] | None = None,
        clock: Callable[[], float] = time.time,
        max_age_s: float = CACHE_MAX_AGE_S,
    ) -> None:
        self._cache_path = cache_path
        self._fetch = fetch if fetch is not None else _default_fetch
        self._clock = clock
        self._max_age_s = max_age_s
        self._lock = threading.Lock()
        self._loaded = False
        self._prices: dict[str, Price] = {}

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
        age = self._clock() - envelope.get("fetched_at", 0)
        if age >= self._max_age_s:
            return None
        return envelope.get("data")

    def _read_stale_cache(self) -> dict | None:
        envelope = self._read_cache_envelope()
        return envelope.get("data") if envelope is not None else None

    def _parse(self, data: dict) -> dict[str, Price]:
        prices: dict[str, Price] = {}
        for entry in data.get("data", []):
            model_id = entry.get("id")
            pricing = entry.get("pricing") or {}
            if not model_id or "prompt" not in pricing or "completion" not in pricing:
                continue
            try:
                prices[model_id] = Price(prompt=float(pricing["prompt"]), completion=float(pricing["completion"]))
            except (TypeError, ValueError):
                continue
        return prices

    def _load(self) -> None:
        if self._loaded:
            return
        data = self._read_fresh_cache()
        if data is None:
            try:
                fetched = self._fetch()
            except Exception:
                fetched = None
            if fetched is not None:
                data = fetched
                try:
                    self._write_cache_atomically(fetched)
                except OSError:
                    pass
            else:
                data = self._read_stale_cache()  # fall back to a stale cache if present
        self._prices = self._parse(data) if data is not None else {}
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
