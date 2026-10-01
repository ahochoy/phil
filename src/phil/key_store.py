"""Provider API key storage: the environment first, then the OS keychain, through `keyring`.

`keyring` is imported lazily inside each function: this module (and everything that imports
it) must stay importable without `keyring` (or its platform backends) being loaded, and this
module itself must not import langgraph, langchain or deepagents at module level.

Values are never printed or logged. `key_source` reports only where a value came from."""

import logging
import os
from collections.abc import Iterator, Mapping
from typing import Literal

SERVICE = "phil"

logger = logging.getLogger(__name__)


class KeyStoreError(Exception):
    pass


def keychain_available() -> bool:
    """False when `keyring`'s active backend is the fail or null backend, or when importing
    `keyring` (or asking it for its backend) fails."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        from keyring.backends.null import Keyring as NullKeyring

        backend = keyring.get_keyring()
    except Exception:
        return False
    return not isinstance(backend, (FailKeyring, NullKeyring))


def _keychain_get(var: str) -> str | None:
    """`var`'s value from the keychain, or None if it has none. Any keyring exception counts
    as None and is logged at debug level, never with the value."""
    try:
        import keyring

        value = keyring.get_password(SERVICE, var)
    except Exception:
        logger.debug("keyring lookup failed for %s", var, exc_info=True)
        return None
    return value or None


def key_source(var: str) -> Literal["env", "keychain"] | None:
    """Where `var`'s value would come from: `env` if `os.environ` has it (non-empty), else
    `keychain` if the keychain has a non-empty value for it, else None."""
    if os.environ.get(var):
        return "env"
    if _keychain_get(var):
        return "keychain"
    return None


def get_key(var: str) -> str | None:
    """`var`'s value: the environment first, then the keychain. None if neither has it."""
    value = os.environ.get(var)
    if value:
        return value
    return _keychain_get(var)


def set_key(var: str, value: str) -> None:
    """Store `value` for `var` in the keychain.

    Raises `KeyStoreError` when no keychain is available here."""
    if not keychain_available():
        raise KeyStoreError(f"No keychain is available here; export {var} instead.")
    import keyring

    keyring.set_password(SERVICE, var, value)


def delete_key(var: str) -> bool:
    """Delete `var`'s keychain entry. True if one was deleted, False if there wasn't one."""
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(SERVICE, var)
        return True
    except PasswordDeleteError:
        return False
    except Exception:
        logger.debug("keyring delete failed for %s", var, exc_info=True)
        return False


class KeyLookup(Mapping[str, str]):
    """A read-only mapping that resolves each variable through `get_key`: the environment
    first, then the keychain. `__iter__`/`__len__` only cover `os.environ` — enough for `.get`,
    which is all that callers (`build_chat_model`, `missing_keys`) use."""

    def __getitem__(self, var: str) -> str:
        value = get_key(var)
        if value is None:
            raise KeyError(var)
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(os.environ)

    def __len__(self) -> int:
        return len(os.environ)


def key_lookup() -> KeyLookup:
    return KeyLookup()
