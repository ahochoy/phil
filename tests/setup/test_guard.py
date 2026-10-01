import pytest

from phil.setup.guard import looks_like_key

PLANTED = "sk-proj-TESTSECRET0123456789abcdefABCDEF"


@pytest.mark.parametrize(
    "text",
    [
        PLANTED,
        "sk-TESTSECRET-123",
        "sk_TESTSECRET",
        "sk-ant-TESTSECRET",
        "sk-or-v1-TESTSECRET",
        "gsk_TESTSECRET",
        "AIzaTESTSECRET",
        "xai-TESTSECRET",
        "pplx-TESTSECRET",
        "r8_TESTSECRET",
        "hf_TESTSECRET",
        "TESTSECRET0123456789abcdefABCDEF0123",  # a long, prefix-less token of letters and digits
        "TESTsecret0123456789TESTsecret0123456789",  # a 40-character mixed-case token
        "openrouter:sk-or-v1-TESTSECRET",  # a key typed after a provider prefix
        f"  {PLANTED}  ",
    ],
)
def test_keys_are_recognised(text):
    assert looks_like_key(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "openrouter:anthropic/claude-sonnet-5",
        "anthropic/claude-sonnet-5",
        "openai/gpt-6-sol",
        "google/gemini-3.8-flash",
        "llama3.1:8b",
        "gemini-2.5-flash-lite-preview-06-17",  # real bare Google ids: long, but all lower case
        "gemini-2.5-flash-preview-native-audio-dialog",
        "ollama:qwen3:32b",
        "gpt-x",
        "claude-sonnet-5",
        "lab",
        "LAB_API_KEY",
        "http://localhost:8000/v1",
        "none",
        "x" * 40,  # long, but no digits
        "a sentence with 0123456789 and many words in it",
    ],
)
def test_model_ids_and_ordinary_answers_pass(text):
    assert not looks_like_key(text)
