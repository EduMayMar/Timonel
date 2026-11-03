from __future__ import annotations

from typing import Any

SERVICE = "timonel"


class KeyringUnavailable(Exception):
    pass


def backend() -> Any:
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as error:
        raise KeyringUnavailable("the keyring package is not installed") from error
    try:
        current = keyring.get_keyring()
    except KeyringError as error:
        raise KeyringUnavailable(str(error)) from error
    if type(current).__name__ in ("Keyring", "FailKeyring") and "fail" in type(current).__module__:
        raise KeyringUnavailable("no system keyring backend is available")
    return keyring


def store(name: str, value: str) -> None:
    try:
        backend().set_password(SERVICE, name, value)
    except KeyringUnavailable:
        raise
    except Exception as error:
        raise KeyringUnavailable(str(error)) from error


def delete(name: str) -> bool:
    try:
        backend().delete_password(SERVICE, name)
    except KeyringUnavailable:
        raise
    except Exception:
        return False
    return True


def present(name: str) -> bool:
    try:
        return backend().get_password(SERVICE, name) is not None
    except KeyringUnavailable:
        return False
    except Exception:
        return False
