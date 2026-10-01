"""Spotting an API key pasted where it doesn't belong, so setup never echoes, writes or sends it."""

KEY_PREFIXES = ("sk-", "sk_", "sk-ant-", "sk-or-", "sk-proj-", "gsk_", "AIza", "xai-", "pplx-", "r8_", "hf_")
LONG_TOKEN = 32
KEY_WARNING = (
    "That looks like an API key — it isn't shown or saved here. "
    "Enter it at the key prompt (or phil keys set <provider>)."
)


def _has_key_prefix(text: str) -> bool:
    return text.startswith(KEY_PREFIXES)


def _is_long_token(text: str) -> bool:
    """A whitespace-free run of 32+ characters mixing letters and digits, with no "/" or ":" —
    so model ids (`openrouter:anthropic/…`) and Ollama tags (`llama3.1:8b`) never match."""
    return (
        len(text) >= LONG_TOKEN
        and not any(char.isspace() for char in text)
        and "/" not in text
        and ":" not in text
        and any(char.isalpha() for char in text)
        and any(char.isdigit() for char in text)
    )


def looks_like_key(text: str) -> bool:
    """Whether `text` looks like an API key: a known key prefix (also after a `provider:` prefix),
    or a long token of letters and digits."""
    text = text.strip()
    if not text:
        return False
    after_prefix = text.split(":", 1)[1] if ":" in text else ""
    return _has_key_prefix(text) or _has_key_prefix(after_prefix) or _is_long_token(text)
