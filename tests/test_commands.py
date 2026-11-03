import json
import socket
from pathlib import Path

import pytest
import yaml

from timonel import keys
from timonel.cli import main
from timonel.config import user_agents_dir, user_config_path

from .conftest import TEST_ROUTES, Fakes, run_git


@pytest.fixture
def env(fakes: Fakes, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for key, value in fakes.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TIMONEL_LANG", "en")
    import timonel.cli as cli
    import timonel.commands as commands

    monkeypatch.setattr(cli.console, "width", 240)
    monkeypatch.setattr(commands.console, "width", 240)
    config = user_config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(TEST_ROUTES, encoding="utf-8")
    return config


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []
    original = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> object:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            attempts.append(address)
            raise AssertionError(f"unsolicited network call to {address}")
        return original(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, *args, **kwargs: (
            attempts.append(address),
            (_ for _ in ()).throw(AssertionError(f"unsolicited network call to {address}")),
        )[1],
    )
    return attempts


def test_no_telemetry_or_unsolicited_network(
    env: Path,
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    no_network: list[object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(repo)
    for args in (
        ("doctor",),
        ("plan", "fix typo in README.md"),
        ("run", "[[edit all:result.txt=good]] add file", "--repo", str(repo)),
        ("status",),
        ("stats",),
        ("config", "list"),
        ("agents", "list"),
        ("learn", "privacy"),
        ("models",),
    ):
        code, out = run(capsys, *args)
        assert code in (0, 1), (args, out)
    assert no_network == []


def test_learn_topics(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "learn")
    assert code == 0 and "worktree" in out
    code, out = run(capsys, "--lang", "es", "learn", "worktree")
    assert "Un worktree de git" in out
    code, out = run(capsys, "--lang", "pt", "learn", "budget")
    assert "orçamento" in out.lower()
    assert run(capsys, "learn", "nope")[0] == 2


def test_errors_show_code_cause_fix_and_link(
    env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(capsys, "run", "x", "--repo", str(tmp_path))
    assert code == 1
    assert "TMN-2001" in out
    assert "How to fix it" in out
    assert "edumaymar.github.io/Timonel/errors/#tmn-2001" in out
    code, out = run(capsys, "--lang", "es", "status", "nope")
    assert "TMN-4001" in out and "Cómo solucionarlo" in out
    code, out = run(capsys, "--set", "execution.total_timeout_s=soon", "status")
    assert code == 2 and "TMN-1001" in out


def test_config_commands_round_trip(
    env: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out = run(capsys, "config", "set", "execution.idle_timeout_s", "42")
    assert code == 0
    assert yaml.safe_load(env.read_text())["execution"]["idle_timeout_s"] == 42
    code, out = run(capsys, "config", "get", "execution.idle_timeout_s")
    assert json.loads(out) == 42
    code, out = run(capsys, "config", "explain", "execution.idle_timeout_s")
    assert "user" in out and "Limit without any output" in out
    code, out = run(capsys, "--lang", "es", "config", "explain", "execution.idle_timeout_s")
    assert "Tiempo máximo sin salida" in out
    code, out = run(capsys, "config", "set", "execution.idle_timeout", "4")
    assert code == 2 and "did you mean" in out
    code, out = run(capsys, "config", "set", "execution.idle_timeout_s", "-4")
    assert code == 2 and "must be at least 1" in out
    code, out = run(capsys, "config", "list", "--json", "--all")
    rows = {row["key"]: row for row in json.loads(out)}
    assert rows["execution.idle_timeout_s"]["layer"] == "user"
    assert rows["execution.total_timeout_s"]["layer"] == "user"
    code, out = run(capsys, "config", "unset", "execution.idle_timeout_s")
    assert code == 0
    assert run(capsys, "config", "unset", "execution.idle_timeout_s")[0] == 1
    exported = tmp_path / "export.yaml"
    assert run(capsys, "config", "export", str(exported))[0] == 0
    assert run(capsys, "config", "import", str(exported))[0] == 0
    code, out = run(capsys, "config", "schema")
    assert json.loads(out)["title"] == "Timonel configuration"
    assert run(capsys, "config", "validate")[0] == 0
    assert run(capsys, "config", "reset")[0] == 0
    assert not env.exists()


def test_profiles_and_mode(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "profiles", "copy", "night", "--from", "cheap")[0] == 0
    assert run(capsys, "profiles", "use", "night")[0] == 0
    code, out = run(capsys, "profiles", "list")
    assert "* night" in out
    assert run(capsys, "profiles", "use", "ghost")[0] == 2
    assert run(capsys, "profiles", "delete", "night")[0] == 2
    assert run(capsys, "config", "set", "default_profile", "balanced")[0] == 0
    assert run(capsys, "profiles", "delete", "night")[0] == 0
    assert run(capsys, "mode", "expert")[0] == 0
    _code, out = run(capsys, "mode")
    assert out.strip() == "expert"


def test_simple_mode_hides_advanced_flags(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    simple = capsys.readouterr().out
    assert "--race" not in simple and "--verify" in simple
    run(capsys, "mode", "expert")
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    assert "--race" in capsys.readouterr().out


def test_trust_flow(env: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".timonel.yaml").write_text(
        "verification:\n  commands: [[python, -c, 'pass']]\n", encoding="utf-8"
    )
    code, out = run(capsys, "status")
    assert "is not trusted" not in out
    code, out = run(capsys, "plan", "x", "--repo", str(repo))
    assert "Ignoring" in out
    code, out = run(capsys, "trust", "--repo", str(repo))
    assert code == 1 and "verify: python -c pass" in out
    assert run(capsys, "trust", "--repo", str(repo), "--yes")[0] == 0
    code, out = run(capsys, "plan", "x", "--repo", str(repo))
    assert "Ignoring" not in out
    assert run(capsys, "trust", "--repo", str(repo), "--revoke")[0] == 0


def test_keys_commands(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store: dict[str, str] = {}
    monkeypatch.setattr(keys, "store", lambda name, value: store.__setitem__(name, value))
    monkeypatch.setattr(keys, "delete", lambda name: store.pop(name, None) is not None)
    monkeypatch.setattr(keys, "present", lambda name: name in store)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "secret-value-123")
    assert run(capsys, "keys", "set", "OPENAI_API_KEY")[0] == 0
    assert store == {"OPENAI_API_KEY": "secret-value-123"}
    _code, out = run(capsys, "keys", "list")
    assert "OPENAI_API_KEY: keyring" in out and "secret-value" not in out
    assert run(capsys, "keys", "delete", "OPENAI_API_KEY")[0] == 0


def test_agents_commands(
    env: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path, fakes: Fakes
) -> None:
    code, out = run(capsys, "agents", "list")
    assert "gemini" in out and "provider" in out
    code, out = run(capsys, "agents", "info", "aider")
    assert json.loads(out)["prompt_via"] == "file"
    assert run(capsys, "agents", "enable", "gemini")[0] == 0
    assert yaml.safe_load(env.read_text())["agents"]["gemini"]["enabled"] is True
    assert run(capsys, "agents", "disable", "gemini")[0] == 0
    connector = tmp_path / "mine.yaml"
    connector.write_text(
        "name: mine\nkind: agent\nagent:\n  command: mine\n  prompt_via: stdin\n", encoding="utf-8"
    )
    assert run(capsys, "agents", "add", str(connector))[0] == 0
    assert (user_agents_dir() / "mine.yaml").exists()
    broken = tmp_path / "broken.yaml"
    broken.write_text("name: broken\nagent:\n  command: x\n  prompt_via: arg\n", encoding="utf-8")
    code, out = run(capsys, "agents", "add", str(broken))
    assert code == 2 and "no token contains {prompt}" in out
    assert not (user_agents_dir() / "broken.yaml").exists()
    assert run(capsys, "agents", "remove", "mine")[0] == 0
    assert run(capsys, "agents", "remove", "claude")[0] == 1
    code, out = run(capsys, "agents", "test", "codex")
    assert "codex" in out


def test_custom_connector_wizard(
    env: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import timonel.commands as commands

    answers = iter(["mytool", "mytool", "run --quiet", "a", "-p", "t", "--model", "y"])
    monkeypatch.setattr(commands, "ask", lambda prompt: next(answers))
    assert run(capsys, "agents", "add")[0] == 0
    saved = yaml.safe_load((user_agents_dir() / "mytool.yaml").read_text())
    assert saved["agent"]["prompt_args"] == ["-p", "{prompt}"]
    assert saved["agent"]["base_args"] == ["run", "--quiet"]
    assert saved["verification"]["status"] == "unverified"


def test_explain_flag(env: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--explain", "plan", "x", "--repo", str(repo))
    assert code == 0
    assert "What this command will do" in out and "not interactive" in out


def test_demo_runs_offline(
    env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], no_network: list[object]
) -> None:
    code, out = run(capsys, "demo", "--path", str(tmp_path / "demo"))
    assert code == 0, out
    assert "succeeded" in out
    assert "verify failed" in out and "verify passed" in out
    assert run_git(tmp_path / "demo" / "project", "status", "--porcelain") == ""
    assert no_network == []
    code, out = run(capsys, "demo", "--path", str(tmp_path / "demo"))
    assert code == 0, out
    assert "succeeded" in out


def test_purge(env: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "run", "[[say:x]] explain app.py", "--repo", str(repo))[0] in (0, 1)
    assert run(capsys, "purge")[0] == 1
    code, out = run(capsys, "purge", "--all")
    assert code == 0 and "Deleted 1 runs" in out


def test_start_dry_run_from_cli(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "start", "--dry-run", "--yes", "--agents", "codex")
    assert code == 0
    assert "Dry run" in out
    code, out = run(capsys, "start", "--yes", "--agents", "ghost")
    assert code == 2 and "Unknown agent" in out


def test_doctor_fix_with_nothing_to_fix(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "doctor", "--fix", "--yes")
    assert "Nothing to fix" in out or code in (0, 1)
