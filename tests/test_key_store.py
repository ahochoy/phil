"""Tests for `phil.key_store`: the environment first, then the OS keychain."""

import logging
import subprocess
import sys

import pytest
from keyring.backends.fail import Keyring as FailKeyring
from keyring.backends.null import Keyring as NullKeyring

from phil.key_store import (
    KeyStoreError,
    delete_key,
    get_key,
    key_lookup,
    key_source,
    keychain_available,
    pending_keys,
    set_key,
)


def test_the_environment_wins_over_the_store(monkeypatch):
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-TESTSECRET-env")
    assert key_source("OPENAI_API_KEY") == "env"
    assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-env"


def test_the_store_is_used_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    assert key_source("OPENAI_API_KEY") == "keychain"
    assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-stored"


def test_an_empty_environment_value_falls_through_to_the_store(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    assert key_source("OPENAI_API_KEY") == "keychain"
    assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-stored"


def test_unset_everywhere_gives_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert key_source("OPENAI_API_KEY") is None
    assert get_key("OPENAI_API_KEY") is None


@pytest.mark.parametrize("unavailable_backend", [FailKeyring, NullKeyring], ids=["fail", "null"])
def test_an_unavailable_store_reports_unavailable_and_set_key_refuses(monkeypatch, unavailable_backend):
    import keyring

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    keyring.set_keyring(unavailable_backend())
    assert keychain_available() is False
    with pytest.raises(
        KeyStoreError, match=r"^No keychain is available here; export OPENAI_API_KEY instead\.$"
    ):
        set_key("OPENAI_API_KEY", "sk-TESTSECRET-nope")
    # lookups fall back to the environment only
    assert key_source("OPENAI_API_KEY") is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-TESTSECRET-env")
    assert key_source("OPENAI_API_KEY") == "env"


@pytest.mark.parametrize("unavailable_backend", [FailKeyring, NullKeyring], ids=["fail", "null"])
def test_delete_key_refuses_when_no_store_is_available(monkeypatch, unavailable_backend):
    import keyring

    keyring.set_keyring(unavailable_backend())
    with pytest.raises(
        KeyStoreError, match=r"^No keychain is available here; export OPENAI_API_KEY instead\.$"
    ):
        delete_key("OPENAI_API_KEY")


def test_delete_key_returns_true_then_false():
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    assert delete_key("OPENAI_API_KEY") is True
    assert delete_key("OPENAI_API_KEY") is False


def test_delete_key_wraps_an_unexpected_keyring_error(monkeypatch):
    import keyring

    def explode(service, username):
        raise RuntimeError("boom")

    monkeypatch.setattr(keyring, "delete_password", explode)
    with pytest.raises(
        KeyStoreError, match=r"^Couldn't remove OPENAI_API_KEY from the keychain: RuntimeError$"
    ):
        delete_key("OPENAI_API_KEY")


def test_set_key_wraps_an_unexpected_keyring_error(monkeypatch):
    import keyring

    def explode(service, username, password):
        raise RuntimeError("boom")

    monkeypatch.setattr(keyring, "set_password", explode)
    with pytest.raises(
        KeyStoreError, match=r"^Couldn't save OPENAI_API_KEY to the keychain: RuntimeError$"
    ):
        set_key("OPENAI_API_KEY", "sk-TESTSECRET-nope")


def test_a_lookup_exception_is_logged_at_debug_level_without_the_value(monkeypatch, caplog):
    import keyring

    def explode(service, username):
        raise RuntimeError("boom")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", explode)
    with caplog.at_level(logging.DEBUG, logger="phil.key_store"):
        assert key_source("OPENAI_API_KEY") is None
        assert get_key("OPENAI_API_KEY") is None
    # Our own log message (not the underlying exception's traceback) names only the variable.
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["keyring lookup failed for OPENAI_API_KEY"] * 2


def test_key_lookup_is_a_read_only_mapping_over_env_then_store(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    lookup = key_lookup()
    assert lookup.get("OPENAI_API_KEY") == "sk-TESTSECRET-stored"
    assert lookup.get("NO_SUCH_VAR_AT_ALL") is None
    with pytest.raises(KeyError):
        lookup["NO_SUCH_VAR_AT_ALL"]


def test_pending_keys_is_used_when_the_variable_is_unset_elsewhere(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pending_keys({"OPENAI_API_KEY": "sk-TESTSECRET-pending"}):
        assert key_source("OPENAI_API_KEY") == "pending"
        assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-pending"


def test_the_environment_beats_pending_and_pending_beats_the_store(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-TESTSECRET-env")
    set_key("OPENAI_API_KEY", "sk-TESTSECRET-stored")
    with pending_keys({"OPENAI_API_KEY": "sk-TESTSECRET-pending"}):
        assert key_source("OPENAI_API_KEY") == "env"
        assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-env"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pending_keys({"OPENAI_API_KEY": "sk-TESTSECRET-pending"}):
        assert key_source("OPENAI_API_KEY") == "pending"
        assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-pending"


def test_pending_keys_is_restored_after_the_block_and_after_an_exception(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pending_keys({"OPENAI_API_KEY": "sk-TESTSECRET-pending"}):
        assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-pending"
    assert get_key("OPENAI_API_KEY") is None

    with pytest.raises(RuntimeError):
        with pending_keys({"OPENAI_API_KEY": "sk-TESTSECRET-pending"}):
            raise RuntimeError("boom")
    assert get_key("OPENAI_API_KEY") is None


def test_pending_keys_nesting_merges(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pending_keys({"OPENAI_API_KEY": "sk-TESTSECRET-outer"}):
        with pending_keys({"ANTHROPIC_API_KEY": "sk-TESTSECRET-inner"}):
            assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-outer"
            assert get_key("ANTHROPIC_API_KEY") == "sk-TESTSECRET-inner"
        assert get_key("OPENAI_API_KEY") == "sk-TESTSECRET-outer"
        assert get_key("ANTHROPIC_API_KEY") is None


def test_a_spawned_child_gets_a_null_keyring_backend():
    # The `memory_keyring` fixture's `set_keyring` call only affects this process: a child
    # spawned the way `spawn_worker` spawns one (a fresh `sys.executable` process inheriting
    # `os.environ`) never sees it, and would otherwise auto-detect the real OS keychain on
    # first use. The fixture instead pins `PYTHON_KEYRING_BACKEND` in `os.environ`, which
    # `keyring` itself honors in every process that inherits it. Print only the backend's type
    # name, never a key value.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import keyring; print(type(keyring.get_keyring()).__module__ + '.' + type(keyring.get_keyring()).__qualname__)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "keyring.backends.null.Keyring"
