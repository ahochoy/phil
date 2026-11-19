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
