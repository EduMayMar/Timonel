import os
import subprocess
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from timonel.config import Config, validate_text
from timonel.engine import Engine
from timonel.ledger import Ledger

FAKE = Path(__file__).parent / "fakes" / "fake_agent.py"
AGENTS = ("claude", "codex", "copilot", "agy")
WINDOWS = os.name == "nt"


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


@pytest.fixture(autouse=True)
def isolated_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> MemoryKeyring:
    from timonel import wizard

    monkeypatch.setenv("TIMONEL_HOME", str(tmp_path_factory.mktemp("timonel-home")))
    for index, (key, value) in enumerate(
        (("commit.gpgsign", "false"), ("init.defaultBranch", "main"))
    ):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    backend = MemoryKeyring()
    install_fake_keyring(monkeypatch, backend)
    monkeypatch.setattr(wizard, "refresh_windows_path", lambda environ: False)
    return backend


def windows_launcher() -> bytes:
    import platform

    import distlib

    name = "t64-arm.exe" if platform.machine().lower() in ("arm64", "aarch64") else "t64.exe"
    return (Path(distlib.__file__).parent / name).read_bytes()


def write_windows_exe(bin_dir: Path, name: str) -> None:
    import io
    import zipfile

    script = (
        "import runpy, sys\n"
        f"sys.argv[1:1] = [{name!r}]\n"
        f"runpy.run_path({str(FAKE)!r}, run_name='__main__')\n"
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("__main__.py", script)
    executable = sys.executable
    interpreter = f'"{executable}"' if " " in executable else executable
    shebang = f"#!{interpreter}\n".encode()
    (bin_dir / f"{name}.exe").write_bytes(windows_launcher() + shebang + archive.getvalue())


def write_shim(bin_dir: Path, name: str) -> None:
    if WINDOWS:
        write_windows_exe(bin_dir, name)
        return
    shim = bin_dir / name
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" {name} "$@"\n', encoding="utf-8")
    shim.chmod(0o755)


@dataclass
class Fakes:
    bin_dir: Path
    state: Path
    env: dict[str, str]

    def calls(self) -> list[dict[str, object]]:
        import json

        path = self.state / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def remove(self, name: str) -> None:
        for candidate in self.bin_dir.glob(f"{name}*"):
            candidate.unlink()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "home"
    target.mkdir()
    monkeypatch.setenv("TIMONEL_HOME", str(target))
    return target


@pytest.fixture
def fakes(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> Fakes:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    for name in AGENTS:
        write_shim(bin_dir, name)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("FAKE_", "DEEPSEEK", "ANTHROPIC", "COPILOT_", "GH_TOKEN", "GITHUB_TOKEN")
        )
    }
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["FAKE_STATE_DIR"] = str(state)
    env["DEEPSEEK_API_KEY"] = "sk-deepseek-secret-value-123456"
    env["GH_TOKEN"] = "ghp_faketokenfaketokenfaketoken1234"
    env["TIMONEL_HOME"] = str(home)
    env["PYTHONUTF8"] = "1"
    return Fakes(bin_dir=bin_dir, state=state, env=env)


TEST_ROUTES = """
execution:
  total_timeout_s: 30
  idle_timeout_s: 10
  cooldown_minutes: 5
adaptive:
  enabled: false
"""


def make_config(extra: str = "") -> Config:
    return validate_text(TEST_ROUTES + extra)


@pytest.fixture
def config() -> Config:
    return make_config()


@pytest.fixture
def ledger(home: Path) -> Iterator[Ledger]:
    store = Ledger(home / "ledger.sqlite")
    yield store
    store.close()


@pytest.fixture
def engine(config: Config, ledger: Ledger, fakes: Fakes) -> Engine:
    return Engine(config, ledger, environ=fakes.env)


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "config", "user.email", "test@example.com")
    run_git(root, "config", "user.name", "Test")
    run_git(root, "config", "commit.gpgsign", "false")
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "init")
    return root
