import sys

import pytest

from timonel import keys, notify
from timonel.config import Notifications

from .conftest import MemoryKeyring, install_fake_keyring


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
