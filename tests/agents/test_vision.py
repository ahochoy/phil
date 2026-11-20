import pytest

from phil.agents.pricing import PriceBook
from phil.agents.providers import ProviderSpec
from phil.agents.vision import sees_images

PRICE = {"prompt": "0.000001", "completion": "0.000002"}
LIST = {
    "data": [
        {"id": "a/b", "pricing": PRICE, "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "a/text", "pricing": PRICE, "architecture": {"input_modalities": ["text"]}},
        {"id": "anthropic/claude-x", "pricing": PRICE, "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "a/bare", "pricing": PRICE},
    ]
}


def book(tmp_path):
    return PriceBook(tmp_path / "models.json", fetch=lambda: LIST, clock=lambda: 1_000_000.0)


def spec(kind, name=None, base_url=None):
    if kind == "ollama":
        return ProviderSpec("ollama", "openai", "http://localhost:11434/v1", None, 0.0, 0.0)
    return ProviderSpec(name or kind, kind, base_url, None, None, None)


def test_sees_images(tmp_path):
    b = book(tmp_path)
    assert sees_images("openrouter:a/b", None, b) is True
    assert sees_images("openrouter:a/text", None, b) is False
    assert sees_images("openrouter:a/unlisted", None, b) is None
    assert sees_images("openrouter:a/bare", None, b) is None  # listed, but says nothing about inputs
    assert sees_images("ollama:llava", spec("ollama"), b) is None


def test_sees_images_through_an_openrouter_provider_spec(tmp_path):
    assert sees_images("openrouter:a/b", spec("openrouter"), book(tmp_path)) is True


def test_a_direct_vendor_is_found_by_its_openrouter_id(tmp_path):
    assert sees_images("anthropic:claude-x", spec("anthropic"), book(tmp_path)) is True
    # A proxy may serve anything, so the list says nothing about it.
    assert sees_images("anthropic:claude-x", spec("anthropic", base_url="http://proxy.local"), book(tmp_path)) is None


# --- the vision switch, the safety net and the refusal (Task 5) ---------------------------------

IMAGE = {"type": "image", "base64": "aGk=", "mime_type": "image/png"}
VISION = "openrouter:anthropic/claude-sonnet-5"
BLIND = "openrouter:inclusionai/ling-3.0-flash"
NOTICE = "↪ image: implementer runs on openrouter:anthropic/claude-sonnet-5"


def chat(name="own"):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    return FakeListChatModel(responses=[name])


def request(messages, model=None):
    from langchain.agents.middleware.types import ModelRequest

    return ModelRequest(model=model if model is not None else chat(), messages=messages, tools=[])


def with_image():
    from langchain_core.messages import HumanMessage

    return [HumanMessage(content=[{"type": "text", "text": "build this"}, IMAGE])]


def text_only():
    from langchain_core.messages import HumanMessage

    return [HumanMessage(content="build this")]


def read_file_history():
    """A history whose only picture is a `read_file` tool result on a PNG."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    call = {"id": "c1", "name": "read_file", "args": {"file_path": "/mock.png"}, "type": "tool_call"}
    return [
        HumanMessage(content="build the page in /mock.png"),
        AIMessage(content="", tool_calls=[call]),
        ToolMessage(content=[IMAGE], tool_call_id="c1", name="read_file"),
    ]


class NotFound(Exception):
    """Like the OpenRouter SDK's 404 for a model with no endpoint taking images."""

    status_code = 404


class Handler:
    """A model-call handler: records the model each request went to; raises the queued errors first."""

    def __init__(self, *errors):
        self.errors = list(errors)
        self.models = []
        self.requests = []

    def __call__(self, req):
        self.models.append(req.model)
        self.requests.append(req)
        if self.errors:
            raise self.errors.pop(0)
        return "response"

    async def acall(self, req):
        return self(req)


def switch(own=BLIND, sees=False, vision_model=None, notices=None):
    from phil.agents.vision import VisionSwitch

    vision = (VISION, lambda: vision_model) if vision_model is not None else None
    return VisionSwitch(own, sees, vision, notices.append if notices is not None else None, agent="implementer")


def test_has_image_finds_picture_parts_in_any_message():
    from langchain_core.messages import HumanMessage

    from phil.agents.vision import has_image

    assert has_image(with_image())
    assert has_image(read_file_history())
    assert has_image([HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,aGk="}}])])
    assert has_image([{"role": "user", "content": [IMAGE]}])
    assert not has_image(text_only())
    assert not has_image([HumanMessage(content=[{"type": "text", "text": "an image of a cat"}])])
    assert not has_image([])


def test_image_call_moves_to_vision_when_model_cant_see():
    vision_model, notices = chat("vision"), []
    middleware, handler = switch(vision_model=vision_model, notices=notices), Handler()
    assert middleware.wrap_model_call(request(with_image()), handler) == "response"
    assert handler.models == [vision_model]
    assert notices == [NOTICE]
    middleware.wrap_model_call(request(read_file_history()), handler)
    assert handler.models == [vision_model, vision_model]
    assert notices == [NOTICE]  # once per agent run


def test_image_call_moves_to_vision_async_too():
    import asyncio

    vision_model, notices = chat("vision"), []
    middleware, handler = switch(vision_model=vision_model, notices=notices), Handler()
    assert asyncio.run(middleware.awrap_model_call(request(with_image()), handler.acall)) == "response"
    asyncio.run(middleware.awrap_model_call(request(with_image()), handler.acall))
    assert handler.models == [vision_model, vision_model]
    assert notices == [NOTICE]


def test_text_only_call_stays_on_own_model():
    import asyncio

    notices = []
    middleware, handler = switch(vision_model=chat("vision"), notices=notices), Handler()
    req = request(text_only())
    middleware.wrap_model_call(req, handler)
    asyncio.run(middleware.awrap_model_call(req, handler.acall))
    assert handler.requests == [req, req]  # the very same request, unchanged
    assert notices == []


def test_a_model_that_sees_keeps_its_image_calls():
    for sees in (True, None):
        middleware, handler = switch(sees=sees, vision_model=chat("vision")), Handler()
        req = request(with_image())
        middleware.wrap_model_call(req, handler)
        assert handler.requests == [req]


def test_sees_can_be_asked_lazily_and_only_once():
    from phil.agents.vision import VisionSwitch

    asked = []

    def sees():
        asked.append(1)
        return False

    vision_model = chat("vision")
    middleware = VisionSwitch(BLIND, sees, (VISION, lambda: vision_model), None, agent="implementer")
    handler = Handler()
    middleware.wrap_model_call(request(text_only()), handler)
    assert asked == []  # a text-only call never asks
    middleware.wrap_model_call(request(with_image()), handler)
    middleware.wrap_model_call(request(with_image()), handler)
    assert asked == [1]
    assert handler.models[1:] == [vision_model, vision_model]


def test_blind_model_without_vision_raises_image_refusal():
    import asyncio

    from phil.agents.failures import Failure, classify_failure
    from phil.agents.vision import ImageRefusal

    handler = Handler()
    with pytest.raises(ImageRefusal) as caught:
        switch().wrap_model_call(request(read_file_history()), handler)
    with pytest.raises(ImageRefusal):
        asyncio.run(switch().awrap_model_call(request(read_file_history()), handler.acall))
    assert handler.models == []  # never sent
    assert caught.value.model == BLIND
    assert classify_failure(caught.value, provider="openrouter") == Failure(
        "refused",
        "openrouter:inclusionai/ling-3.0-flash can't read images.",
        "Phil won't retry this.",
        'Set a vision model: [models] vision = "provider:model"',
    )


def test_unknown_model_refused_then_retried_on_vision():
    import asyncio

    own, vision_model, notices = chat("own"), chat("vision"), []
    middleware = switch(own="ollama:llama3", sees=None, vision_model=vision_model, notices=notices)
    handler = Handler(NotFound("No endpoints found that support image input"))
    assert middleware.wrap_model_call(request(with_image(), own), handler) == "response"
    assert handler.models == [own, vision_model]
    assert notices == ["↪ image: implementer runs on openrouter:anthropic/claude-sonnet-5"]
    middleware = switch(own="ollama:llama3", sees=None, vision_model=vision_model, notices=notices)
    handler = Handler(NotFound("No endpoints found that support image input"))
    assert asyncio.run(middleware.awrap_model_call(request(with_image(), own), handler.acall)) == "response"
    assert handler.models == [own, vision_model]
    assert len(notices) == 2  # one per agent run (a fresh switch)


def test_a_refusal_keeps_later_image_calls_on_vision():
    import asyncio

    own, vision_model = chat("own"), chat("vision")
    for run in ("sync", "async"):
        notices = []
        middleware = switch(own="ollama:llama3", sees=None, vision_model=vision_model, notices=notices)
        handler = Handler(NotFound("No endpoints found that support image input"))
        for _ in range(2):
            if run == "sync":
                assert middleware.wrap_model_call(request(with_image(), own), handler) == "response"
            else:
                assert asyncio.run(middleware.awrap_model_call(request(with_image(), own), handler.acall)) == "response"
        assert handler.models == [own, vision_model, vision_model]  # the blind model saw it once in total
        assert notices == [NOTICE]


def test_refusal_not_retried_twice():
    import asyncio

    from phil.agents.vision import ImageRefusal

    own, vision_model = chat("own"), chat("vision")
    for run in ("sync", "async"):
        handler = Handler(NotFound("does not support image input"), NotFound("Image input is not supported"))
        middleware = switch(own="ollama:llama3", sees=None, vision_model=vision_model)
        with pytest.raises(ImageRefusal) as caught:
            if run == "sync":
                middleware.wrap_model_call(request(with_image(), own), handler)
            else:
                asyncio.run(middleware.awrap_model_call(request(with_image(), own), handler.acall))
        assert handler.models == [own, vision_model]
        assert caught.value.model == VISION


def test_a_switched_call_refused_by_vision_is_not_retried():
    from phil.agents.vision import ImageRefusal

    vision_model = chat("vision")
    handler = Handler(NotFound("vision is not supported"))
    with pytest.raises(ImageRefusal) as caught:
        switch(vision_model=vision_model).wrap_model_call(request(with_image()), handler)
    assert handler.models == [vision_model]
    assert caught.value.model == VISION


def test_a_refusal_without_a_vision_model_becomes_an_image_refusal():
    from phil.agents.vision import ImageRefusal

    error = NotFound("No endpoints found that support image input")
    handler = Handler(error)
    with pytest.raises(ImageRefusal) as caught:
        switch(own="ollama:llama3", sees=None).wrap_model_call(request(with_image()), handler)
    assert caught.value.model == "ollama:llama3"
    assert caught.value.__cause__ is error
    assert len(handler.models) == 1


def test_no_retry_when_vision_is_the_model_that_failed():
    from phil.agents.vision import ImageRefusal, VisionSwitch

    handler = Handler(NotFound("does not support image input"))
    middleware = VisionSwitch(VISION, None, (VISION, lambda: chat("vision")), None, agent="implementer")
    with pytest.raises(ImageRefusal):
        middleware.wrap_model_call(request(with_image()), handler)
    assert len(handler.models) == 1


def test_other_errors_pass_through_untouched():
    class TooMany(Exception):
        status_code = 429

    for error, messages in (
        (TooMany("multimodal endpoints are busy"), with_image()),  # transient: never an image refusal
        (NotFound("model not found"), with_image()),  # not about images
        (NotFound("does not support image input"), text_only()),  # the request carried no image
    ):
        handler = Handler(error)
        with pytest.raises(type(error)) as caught:
            switch(own="ollama:llama3", sees=None, vision_model=chat("vision")).wrap_model_call(request(messages), handler)
        assert caught.value is error
        assert len(handler.models) == 1


def test_is_image_refusal():
    import json

    import httpx

    from phil.agents.vision import is_image_refusal

    class Upstream(Exception):
        status_code = 400
        body = json.dumps({"error": {"message": "Provider returned error", "metadata": {
            "provider_name": "Lab", "raw": "This model DOES NOT SUPPORT IMAGE inputs"}}})

    class Server(Exception):
        status_code = 503

    assert is_image_refusal(NotFound("No endpoints found that support image input"))
    assert is_image_refusal(ValueError("Image input is not supported for this model"))
    assert is_image_refusal(Upstream("Provider returned error"))
    assert not is_image_refusal(NotFound("model not found"))
    assert not is_image_refusal(Server("multimodal backend unavailable"))
    assert not is_image_refusal(httpx.ReadTimeout("multimodal request timed out"))


def test_a_vision_served_call_is_recorded_with_its_model_and_provider():
    import uuid

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from phil.agents.collector import UsageCollector

    def model(name):
        usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        return GenericFakeChatModel(messages=iter([AIMessage(content="ok", usage_metadata=usage,
                                                             response_metadata={"model_name": name})]))

    own, vision_model = model("inclusionai/ling-3.0-flash"), model("anthropic/claude-sonnet-5")
    # The switch builds (and tags) the vision model on the first call that moves, then the handler runs it.
    switch(vision_model=vision_model).wrap_model_call(request(with_image()), lambda req: req.model)
    collector = UsageCollector()
    own.invoke("hi", config={"callbacks": [collector]})
    vision_model.invoke("hi", config={"callbacks": [collector], "run_id": uuid.uuid4()})
    first, second = collector.calls
    assert (first.model, first.provider) == ("inclusionai/ling-3.0-flash", None)
    assert (second.model, second.provider) == (VISION, "openrouter")
    assert (second.input_tokens, second.output_tokens) == (10, 2)


# --- the vision model is built only when a call needs it (findings r1) --------------------------


class Builder:
    """A zero-argument vision-model factory that counts its calls; raises `error` if given."""

    def __init__(self, model=None, error=None):
        self.model, self.error, self.calls = model, error, 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.model


def missing_key():
    from phil.config import ConfigError

    return ConfigError("openrouter needs OPENROUTER_API_KEY (used by vision).")


@pytest.mark.parametrize("run", ["sync", "async"])
def test_a_vision_model_that_cant_be_built_leaves_text_only_calls_alone(run):
    import asyncio

    from phil.agents.vision import VisionSwitch

    builder = Builder(error=missing_key())
    middleware = VisionSwitch(BLIND, False, (VISION, builder), None, agent="implementer")
    handler = Handler()
    req = request(text_only())
    if run == "sync":
        assert middleware.wrap_model_call(req, handler) == "response"
    else:
        assert asyncio.run(middleware.awrap_model_call(req, handler.acall)) == "response"
    assert handler.requests == [req]
    assert builder.calls == 0


@pytest.mark.parametrize("run", ["sync", "async"])
def test_an_image_call_to_a_blind_model_surfaces_the_build_error(run):
    import asyncio

    from phil.agents.failures import classify_failure
    from phil.agents.vision import VisionSwitch
    from phil.config import ConfigError

    error = missing_key()
    middleware = VisionSwitch(BLIND, False, (VISION, Builder(error=error)), None, agent="implementer")
    handler = Handler()
    with pytest.raises(ConfigError) as caught:
        if run == "sync":
            middleware.wrap_model_call(request(with_image()), handler)
        else:
            asyncio.run(middleware.awrap_model_call(request(with_image()), handler.acall))
    assert caught.value is error
    assert handler.models == []  # never sent
    failure = classify_failure(caught.value)
    assert (failure.category, failure.headline) == ("config", "openrouter needs OPENROUTER_API_KEY (used by vision).")


@pytest.mark.parametrize("run", ["sync", "async"])
def test_the_safety_net_surfaces_the_build_error_too(run):
    import asyncio

    from phil.agents.vision import VisionSwitch
    from phil.config import ConfigError

    error = missing_key()
    middleware = VisionSwitch("ollama:llama3", None, (VISION, Builder(error=error)), None, agent="implementer")
    handler = Handler(NotFound("No endpoints found that support image input"))
    with pytest.raises(ConfigError) as caught:
        if run == "sync":
            middleware.wrap_model_call(request(with_image()), handler)
        else:
            asyncio.run(middleware.awrap_model_call(request(with_image()), handler.acall))
    assert caught.value is error
    assert len(handler.models) == 1


@pytest.mark.parametrize("run", ["sync", "async"])
def test_the_vision_model_is_built_once_across_image_calls(run):
    import asyncio

    from phil.agents.vision import VisionSwitch

    vision_model = chat("vision")
    builder = Builder(model=vision_model)
    middleware = VisionSwitch(BLIND, False, (VISION, builder), None, agent="implementer")
    handler = Handler()
    for _ in range(2):
        if run == "sync":
            middleware.wrap_model_call(request(with_image()), handler)
        else:
            asyncio.run(middleware.awrap_model_call(request(with_image()), handler.acall))
    assert builder.calls == 1
    assert handler.models == [vision_model, vision_model]


def test_a_failed_build_is_not_tried_again():
    from phil.agents.vision import VisionSwitch
    from phil.config import ConfigError

    builder = Builder(error=missing_key())
    middleware = VisionSwitch(BLIND, False, (VISION, builder), None, agent="implementer")
    for _ in range(2):
        with pytest.raises(ConfigError):
            middleware.wrap_model_call(request(with_image()), Handler())
    assert builder.calls == 1
