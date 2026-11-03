import sys
import types
from typing import Any

import pytest

from timonel import keys, notify
from timonel.config import Notifications


class MemoryKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, name: str, value: str) -> None:
        self.data[(service, name)] = value

    def get_password(self, service: str, name: str) -> str | None:
        return self.data.get((service, name))

    def delete_password(self, service: str, name: str) -> None:
        if (service, name) not in self.data:
            raise KeyError(name)
        del self.data[(service, name)]


def install_fake_keyring(monkeypatch: pytest.MonkeyPatch, backend: Any) -> None:
    module = types.ModuleType("keyring")
    errors = types.ModuleType("keyring.errors")

    class KeyringError(Exception):
        pass

    errors.KeyringError = KeyringError  # type: ignore[attr-defined]
    module.get_keyring = lambda: backend  # type: ignore[attr-defined]
    for name in ("set_password", "get_password", "delete_password"):
        setattr(module, name, getattr(backend, name))
    monkeypatch.setitem(sys.modules, "keyring", module)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)


def test_keys_store_present_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_keyring(monkeypatch, MemoryKeyring())
    assert not keys.present("A_KEY")
    keys.store("A_KEY", "value")
    assert keys.present("A_KEY")
    assert keys.delete("A_KEY")
    assert not keys.delete("A_KEY")


def test_missing_or_failing_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(keys.KeyringUnavailable):
        keys.store("A", "b")
    assert keys.present("A") is False

    class Broken(MemoryKeyring):
        def set_password(self, service: str, name: str, value: str) -> None:
            raise RuntimeError("locked")

    install_fake_keyring(monkeypatch, Broken())
    with pytest.raises(keys.KeyringUnavailable, match="locked"):
        keys.store("A", "b")


def test_notifications_respect_events(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        notify.shutil, "which", lambda name: "/usr/bin/" + name if name == "notify-send" else None
    )
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.os, "name", "posix")
    monkeypatch.setattr(notify.subprocess, "run", lambda command, **kwargs: calls.append(command))
    settings = Notifications(events=("failed",), desktop=True, sound=True)
    notify.notify(settings, {"id": "r1", "status": "succeeded"})
    assert calls == []
    notify.notify(settings, {"id": "r1", "status": "failed"})
    assert calls == [["notify-send", "Timonel", "Timonel run r1: failed"]]
    assert "\a" in capsys.readouterr().err
