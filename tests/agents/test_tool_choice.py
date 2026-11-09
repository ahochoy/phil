import pytest
from pydantic import BaseModel

from phil.agents.providers import BUILTIN_PROVIDERS, ProviderSpec, build_chat_model
from phil.agents.tool_choice import unforced_choice

FAKE_ENVIRON = {
    "OPENROUTER_API_KEY": "dummy-openrouter",
    "OPENAI_API_KEY": "dummy-openai",
    "ANTHROPIC_API_KEY": "dummy-anthropic",
    "GOOGLE_API_KEY": "dummy-google",
}


class Echo(BaseModel):
    """Echo a word back."""

    word: str


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ("any", "auto"),
        ("required", "auto"),
        (True, "auto"),
        ("Echo", "auto"),
        ({"type": "tool", "name": "Echo"}, "auto"),
        ({"type": "function", "function": {"name": "Echo"}}, "auto"),
        ({"type": "any"}, "auto"),
        (None, None),
        (False, False),
        ("auto", "auto"),
        ("none", "none"),
        ({"type": "auto"}, {"type": "auto"}),
    ],
)
def test_unforced_choice(given, sent):
    assert unforced_choice(given) == sent


SPECS = {
    "openai": BUILTIN_PROVIDERS["openai"],
    "ollama": BUILTIN_PROVIDERS["ollama"],
    "custom": ProviderSpec("lab", "openai", "http://lab:8000/v1", None, None, None),
    "anthropic": BUILTIN_PROVIDERS["anthropic"],
    "google": BUILTIN_PROVIDERS["google"],
    "openrouter": BUILTIN_PROVIDERS["openrouter"],
}


@pytest.mark.parametrize("kind", sorted(SPECS))
@pytest.mark.parametrize("forced", ["any", "required", "Echo"])
def test_every_provider_kind_sends_auto_instead_of_a_forced_choice(kind, forced):
    model = build_chat_model(SPECS[kind], "some-model", 30, environ=FAKE_ENVIRON)
    assert model.bind_tools([Echo], tool_choice=forced).kwargs == model.bind_tools([Echo], tool_choice="auto").kwargs


@pytest.mark.parametrize("kind", sorted(SPECS))
def test_an_unforced_choice_passes_through(kind):
    model = build_chat_model(SPECS[kind], "some-model", 30, environ=FAKE_ENVIRON)
    base = type(model).__mro__[2]  # Phil<Base> -> NoForcedToolChoice -> <Base>
    plain = base.bind_tools(model, [Echo], tool_choice="auto")
    assert model.bind_tools([Echo], tool_choice="auto").kwargs == plain.kwargs
