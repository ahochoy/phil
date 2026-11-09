"""Suggested `high`/`low` models per provider, in `provider:model` form, shown as setup's defaults.

Ollama and custom providers have no suggestions: Ollama lists what is installed, and a custom
provider's models are whatever it serves.

Checked against each provider's docs on 2026-09-30:
- OpenRouter: the benchmark's models (`google/gemini-flash-latest` also passed a live check).
- OpenAI: the GPT-5.6 family, `gpt-5.6-sol` (frontier) and `gpt-5.6-terra` (balanced cost).
- Anthropic: the Claude 5.5 family, `claude-opus-5-5` (flagship) and `claude-haiku-5-5` (fastest);
  confirmed live 2026-10-22, with Phil no longer forcing tool calls.
- Google: `gemini-4-argon` and `gemini-3.8-flash`, confirmed live 2026-10-22.
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
        "high": "anthropic:claude-opus-5-5",
        "low": "anthropic:claude-haiku-5-5",
    },
    "google": {
        "high": "google:gemini-4-argon",
        "low": "google:gemini-3.8-flash",
    },
}
