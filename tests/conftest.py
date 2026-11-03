import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from timonel.config import Config, validate_text
from timonel.engine import Engine
from timonel.ledger import Ledger

FAKE = Path(__file__).parent / "fakes" / "fake_agent.py"
AGENTS = ("claude", "codex", "copilot", "agy")
WINDOWS = os.name == "nt"


def write_shim(bin_dir: Path, name: str) -> None:
    if WINDOWS:
        (bin_dir / f"{name}.cmd").write_text(
            f'@"{sys.executable}" "{FAKE}" {name} %*\r\n', encoding="utf-8"
        )
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
    text = TEST_ROUTES + extra
    if WINDOWS:
        text += "\nagents:\n  copilot:\n    prompt_via: stdin\n    prompt_args: []\n"
    return validate_text(text)


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
