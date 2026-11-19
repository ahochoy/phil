"""Can a model read images, and making sure a call that carries one reaches a model that can (spec
2026-11-19 images, "Reaching a model that can see").

`VisionSwitch` imports langchain: this module is loaded lazily (inside invoke_agent and the factory)."""

import threading
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from phil.agents.pricing import PriceBook, book_ids
from phil.agents.providers import ProviderSpec, split_model

# Set on the vision chat model's LangChain `metadata`, so every call it serves reaches the usage
# collector's callbacks tagged with the model that served it (`phil.agents.collector`).
SERVED_BY_KEY = "phil_served_by"

_IMAGE_TYPES = {"image", "image_url"}


def sees_images(model: str, provider: ProviderSpec | None, book: PriceBook) -> bool | None:
    """True or False from the list's `input_modalities` for `model` (`provider:name`, on `provider`;
    None when it has no spec and is `openrouter:<id>`); None when unknown: a provider the list doesn't
    cover (a local server, a proxy), a model it doesn't list, or an entry that gives no input modalities."""
    prefix, name = split_model(model)
    kind = provider.kind if provider is not None else prefix
    base_url = provider.base_url if provider is not None else None
    modalities = book.modalities(book_ids(kind, base_url, name))
    return None if modalities is None else "image" in modalities


def has_image(messages: list[Any]) -> bool:
    """True when any message's content (tool results included) holds an `image` or `image_url` part."""
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") in _IMAGE_TYPES for part in content
        ):
            return True
    return False


class ImageRefusal(Exception):
    """A call carried an image that `model` can't read, and no model that can was there to take it."""

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.model = model

    def __str__(self) -> str:
        return f"{self.model} can't read images."


def is_image_refusal(exc: BaseException) -> bool:
    """True when a provider refused a call because of its image: the provider's own reason, or the
    error's text, holds one of `IMAGE_REFUSAL_PHRASES` (OpenRouter's "No endpoints found that
    support image input" 404 among them). Never for a transient error (429, 5xx, a timeout)."""
    from phil.agents.failures import IMAGE_REFUSAL_PHRASES, _status
    from phil.agents.retry import TRANSIENT_STATUS, is_transient, provider_detail

    status = _status(exc)
    if status is not None and (status in TRANSIENT_STATUS or status >= 500):
        return False
    if is_transient(exc):
        return False
    text = f"{provider_detail(exc) or ''} {exc}".lower()
    return any(phrase in text for phrase in IMAGE_REFUSAL_PHRASES)


class _Once:
    """Calls `notice` with the first text it's given, and never again (thread-safe: sub-agents run in parallel)."""

    def __init__(self, notice: Callable[[str], None] | None) -> None:
        self.notice = notice
        self._lock = threading.Lock()
        self._sent = False

    def __call__(self, text: str) -> None:
        if self.notice is None:
            return
        with self._lock:
            if self._sent:
                return
            self._sent = True
        self.notice(text)


class VisionSwitch(AgentMiddleware):
    """Sends a model call that carries an image to a model that can see it.

    - The agent's own model is known not to see (`sees` is False): the call runs on `vision`
      (`(model string, chat model)`), or raises `ImageRefusal` when there is none.
    - Otherwise the call runs as it is; if the provider refuses it over the image, it is retried
      once on `vision` when that is a different model, else it raises `ImageRefusal`.

    A call without an image is passed through untouched. `notice` gets one line per agent run the
    first time a call moves: `↪ image: <agent> runs on <vision model>`. `sees` may be a function,
    asked once, on the first call that carries an image (it may read the price list).

    The first middleware of every harness, outside the per-call retry: a refusal isn't transient,
    so it passes straight through `PhilModelRetryMiddleware`, and the vision call gets the
    transient retries too."""

    def __init__(
        self,
        own_model: str,
        sees: "bool | None | Callable[[], bool | None]",
        vision: tuple[str, Any] | None,
        notice: Callable[[str], None] | None,
        *,
        agent: str = "agent",
    ) -> None:
        super().__init__()
        self.own_model = own_model
        self.vision = vision
        self.notice = notice
        self.agent = agent
        self._sees = sees
        self._sees_lock = threading.Lock()
        self._once = _Once(notice)
        if vision is not None:
            _tag_served_by(vision[1], vision[0])

    def _known_blind(self) -> bool:
        with self._sees_lock:
            if callable(self._sees):
                self._sees = self._sees()
            return self._sees is False

    def _on_vision(self, request: Any) -> Any:
        assert self.vision is not None
        from phil.text import printable

        self._once(f"↪ image: {self.agent} runs on {printable(self.vision[0])}")
        return request.override(model=self.vision[1])

    def _can_retry(self) -> bool:
        return self.vision is not None and self.vision[0] != self.own_model

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if not has_image(request.messages):
            return handler(request)
        if self._known_blind():
            if self.vision is None:
                raise ImageRefusal(self.own_model)
            return _refused_as_image(self.vision[0], handler, self._on_vision(request))
        try:
            return handler(request)
        except Exception as exc:
            if not is_image_refusal(exc):
                raise
            if not self._can_retry():
                raise ImageRefusal(self.own_model) from exc
        assert self.vision is not None
        return _refused_as_image(self.vision[0], handler, self._on_vision(request))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        if not has_image(request.messages):
            return await handler(request)
        if self._known_blind():
            if self.vision is None:
                raise ImageRefusal(self.own_model)
            return await _arefused_as_image(self.vision[0], handler, self._on_vision(request))
        try:
            return await handler(request)
        except Exception as exc:
            if not is_image_refusal(exc):
                raise
            if not self._can_retry():
                raise ImageRefusal(self.own_model) from exc
        assert self.vision is not None
        return await _arefused_as_image(self.vision[0], handler, self._on_vision(request))


def _refused_as_image(model: str, handler: Callable[[Any], Any], request: Any) -> Any:
    """`handler(request)` on the vision model: an image refusal there is final, as `ImageRefusal`."""
    try:
        return handler(request)
    except Exception as exc:
        if is_image_refusal(exc):
            raise ImageRefusal(model) from exc
        raise


async def _arefused_as_image(model: str, handler: Callable[[Any], Awaitable[Any]], request: Any) -> Any:
    try:
        return await handler(request)
    except Exception as exc:
        if is_image_refusal(exc):
            raise ImageRefusal(model) from exc
        raise


def _tag_served_by(chat_model: Any, model: str) -> None:
    """Mark `chat_model`'s calls as served by `model` (in its LangChain run metadata)."""
    try:
        chat_model.metadata = {**(getattr(chat_model, "metadata", None) or {}), SERVED_BY_KEY: model}
    except (AttributeError, TypeError, ValueError):  # not a LangChain model (a test stand-in)
        pass
