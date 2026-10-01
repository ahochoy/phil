"""Tests for `tests/conftest.py`'s `memory_keyring` fixture and the backend it installs.

Covers the offline path directly (an unmarked test gets a fresh, writable, in-memory backend,
with `PYTHON_KEYRING_BACKEND` pinned to the null backend for child processes) and the
`ReadOnlyKeyring` passthrough live/bench tests get instead, built over a planted in-memory
backend rather than the real keychain. The live-marker branch itself is covered through
`_select_keyring`, the selection helper, not a live-marked test."""

import os

import pytest
from keyring.errors import PasswordDeleteError, PasswordSetError

from tests.conftest import MemoryKeyring, ReadOnlyKeyring, _select_keyring


def test_offline_tests_get_a_fresh_writable_in_memory_backend(memory_keyring):
    assert isinstance(memory_keyring, MemoryKeyring)
    memory_keyring.set_password("svc", "user", "sk-TESTSECRET")
    assert memory_keyring.get_password("svc", "user") == "sk-TESTSECRET"


def test_offline_tests_pin_the_null_backend_for_child_processes():
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"


def test_read_only_keyring_delegates_get_password_to_the_inner_backend():
    inner = MemoryKeyring()
    inner.set_password("svc", "user", "sk-TESTSTORED")
    passthrough = ReadOnlyKeyring(inner)

    assert passthrough.get_password("svc", "user") == "sk-TESTSTORED"


def test_read_only_keyring_refuses_set_password_and_leaves_the_inner_store_unchanged():
    inner = MemoryKeyring()
    passthrough = ReadOnlyKeyring(inner)

    with pytest.raises(PasswordSetError):
        passthrough.set_password("svc", "user", "sk-TESTNEW")
    assert inner.store == {}


def test_read_only_keyring_refuses_delete_password_and_leaves_the_inner_store_unchanged():
    inner = MemoryKeyring()
    inner.set_password("svc", "user", "sk-TESTSTORED")
    passthrough = ReadOnlyKeyring(inner)

    with pytest.raises(PasswordDeleteError):
        passthrough.delete_password("svc", "user")
    assert inner.store == {("svc", "user"): "sk-TESTSTORED"}


def test_select_keyring_installs_a_fresh_memory_backend_when_not_live():
    real_backend = MemoryKeyring()
    real_backend.set_password("svc", "user", "sk-TESTREAL")

    backend = _select_keyring(is_live=False, real_backend=real_backend)

    assert isinstance(backend, MemoryKeyring)
    assert backend is not real_backend
    assert backend.get_password("svc", "user") is None  # a fresh store, not the real one


def test_select_keyring_installs_a_read_only_passthrough_over_the_real_backend_when_live():
    real_backend = MemoryKeyring()
    real_backend.set_password("svc", "user", "sk-TESTREAL")

    backend = _select_keyring(is_live=True, real_backend=real_backend)

    assert isinstance(backend, ReadOnlyKeyring)
    assert backend.get_password("svc", "user") == "sk-TESTREAL"
    with pytest.raises(PasswordSetError):
        backend.set_password("svc", "user", "sk-TESTNEW")
