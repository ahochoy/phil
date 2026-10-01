"""Suggested `high`/`low` models per provider, in `provider:model` form, shown as setup's defaults.

Ollama and custom providers have no suggestions: Ollama lists what is installed, and a custom
provider's models are whatever it serves.

Checked against each provider's docs on 2026-09-30:
- OpenRouter: the benchmark's models (`google/gemini-flash-latest` also passed a live check).
- OpenAI: the GPT-5.6 family, `gpt-5.6-sol` (frontier) and `gpt-5.6-terra` (balanced cost).
- Anthropic: `claude-opus-5` (flagship) and `claude-haiku-4-5` (fastest).
- Google is left out until its current Pro model ID is confirmed; setup then asks for both IDs.
"""

SUGGESTIONS: dict[str, dict[str, str]] = {
    "openrouter": {
        "high": "openrouter:anthropic/claude-sonnet-5",
        "low": "openrouter:google/gemini-3.8-flash",
    },
    "openai": {
        "high": "openai:gpt-5.6-sol",
        "low": "openai:gpt-5.6-terra",
    },
    "anthropic": {
        "high": "anthropic:claude-opus-5",
        "low": "anthropic:claude-haiku-4-5",
    },
}
