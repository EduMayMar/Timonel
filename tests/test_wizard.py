import json
import os
import sys
import types
from pathlib import Path

import pytest

from timonel import keys, wizard
from timonel.config import Config

from .conftest import WINDOWS, make_config

MANAGER = """import pathlib, sys
log = pathlib.Path(sys.argv[1])
log.open('a').write(' '.join(sys.argv[2:]) + '\\n')
target = sys.argv[3] if len(sys.argv) > 3 else ''
"""


def fake_program(folder: Path, name: str, body: str) -> None:
    script = folder / f"{name}_impl.py"
    script.write_text(body, encoding="utf-8")
    if WINDOWS:
        (folder / f"{name}.bat").write_text(
            f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        shim = folder / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)


@pytest.fixture
def system(tmp_path: Path, home: Path) -> dict[str, object]:
    bin_dir = tmp_path / "sim-bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    env = {
        "PATH": str(bin_dir),
        "TIMONEL_HOME": str(home),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    return {"bin": bin_dir, "log": log, "env": env}


def session_for(system: dict[str, object], answers: list[str], **options: object) -> wizard.Session:
    said: list[str] = []
    pending = list(answers)
    bin_dir = system["bin"]
    log = system["log"]

    def runner(argv: list[str]) -> int:
        with Path(str(log)).open("a", encoding="utf-8") as handle:
            handle.write(" ".join(argv) + "\n")
        if argv[:2] == ["npm", "install"]:
            package = argv[-1].split("/")[-1]
            command = {
                "claude-code": "claude",
                "codex": "codex",
                "copilot": "copilot",
                "gemini-cli": "gemini",
            }.get(package, package)
            fake_program(Path(str(bin_dir)), command, "print('installed fake 1.0')\n")
        if argv[:1] == ["brew"] or argv[:1] == ["winget"]:
            fake_program(
                Path(str(bin_dir)),
                "npm" if "node" in " ".join(argv).lower() else "git",
                "print('ok')\n",
            )
        return 0

    session = wizard.Session(
        ask=lambda prompt: pending.pop(0) if pending else "s",
        say=said.append,
        runner=runner,
        environ=dict(system["env"]),  # type: ignore[arg-type]
        fetch=lambda url: b"#!/bin/sh\necho installing\n",
        **options,  # type: ignore[arg-type]
    )
    session.said = said  # type: ignore[attr-defined]
    return session


def config_with_reachable_network(monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setattr(wizard, "reachable", lambda host, environ: True)
    return make_config()


def test_detects_os_and_package_managers(system: dict[str, object]) -> None:
    assert wizard.detect_os("Windows") == "windows"
    assert wizard.detect_os("Darwin") == "macos"
    assert wizard.detect_os("Linux") == "linux"
    for name in ("brew", "npm", "apt-get"):
        fake_program(system["bin"], name, "")  # type: ignore[arg-type]
    assert wizard.detect_managers(system["env"]) == ["brew", "apt", "npm"]  # type: ignore[arg-type]


def test_full_install_flow_on_macos(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    fake_program(system["bin"], "brew", "")  # type: ignore[arg-type]
    session = session_for(system, ["i", "i", "i", "s"])
    results = wizard.run_wizard(config, session, ["claude"], system="Darwin")
    log = Path(str(system["log"])).read_text().splitlines()
    assert log[0] == "brew install git"
    assert "brew install node" in log
    assert "npm install -g @anthropic-ai/claude-code" in log
    assert results["git"] == "installed"
    assert results["claude"] == "installed"
    audit = [json.loads(line) for line in session.audit_path.read_text().splitlines()]
    assert {entry["component"] for entry in audit} >= {"git", "node", "claude"}
    assert any(
        entry["undo"] == ["npm", "uninstall", "-g", "@anthropic-ai/claude-code"] for entry in audit
    )
    said = "\n".join(session.said)  # type: ignore[attr-defined]
    assert "Git keeps the history" in said
    assert "~300 MB" in said
    assert "To undo: npm uninstall -g @anthropic-ai/claude-code" in said


def test_dry_run_changes_nothing(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    fake_program(system["bin"], "brew", "")  # type: ignore[arg-type]
    session = session_for(system, ["i"] * 6, dry_run=True)
    results = wizard.run_wizard(config, session, ["codex"], system="Darwin")
    assert not Path(str(system["log"])).exists()
    assert results["git"] == "dry_run"
    assert not session.state_path.exists()
    assert all(json.loads(line)["dry_run"] for line in session.audit_path.read_text().splitlines())


def test_yes_mode_only_runs_allowed_components(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    fake_program(system["bin"], "brew", "")  # type: ignore[arg-type]
    session = session_for(system, [], assume_yes=True, allow=frozenset({"git"}))
    results = wizard.run_wizard(config, session, ["codex"], system="Darwin")
    log = Path(str(system["log"])).read_text().splitlines()
    assert log == ["brew install git"]
    assert results["node"] == "skipped"
    assert results["codex"] == "skipped"


def test_never_elevates_on_linux(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    fake_program(system["bin"], "apt-get", "")  # type: ignore[arg-type]
    session = session_for(system, ["i", ""])
    results = wizard.run_wizard(config, session, [], system="Linux")
    assert not Path(str(system["log"])).exists()
    said = "\n".join(session.said)  # type: ignore[attr-defined]
    assert "will not run it by itself" in said
    assert "sudo apt-get install -y git" in said
    assert results["git"] == "not_found"


def test_resumes_from_state_file(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    fake_program(system["bin"], "brew", "")  # type: ignore[arg-type]
    skipped = session_for(system, ["s"])
    wizard.run_wizard(config, skipped, [], system="Darwin")
    assert not skipped.state_path.exists()
    first = session_for(system, ["i"])
    wizard.run_wizard(config, first, [], system="Darwin")
    assert json.loads(first.state_path.read_text())["done"] == ["install:git"]
    for leftover in Path(str(system["bin"])).glob("git*"):
        leftover.unlink()
    second = session_for(system, [])
    results = wizard.run_wizard(config, second, [], system="Darwin")
    assert results["git"] == "done_before"
    assert any("Resuming" in line for line in second.said)  # type: ignore[attr-defined]


def test_offline_and_no_manager_messages(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard, "reachable", lambda host, environ: False)
    config = make_config()
    fake_program(system["bin"], "npm", "")  # type: ignore[arg-type]
    fake_program(system["bin"], "git", "")  # type: ignore[arg-type]
    session = session_for(system, ["i"])
    results = wizard.run_wizard(config, session, ["codex"], system="Windows")
    assert results["codex"] == "offline"
    said = "\n".join(session.said)  # type: ignore[attr-defined]
    assert "Cannot reach registry.npmjs.org" in said
    empty = session_for({**system, "env": {"PATH": str(Path(str(system["bin"])) / "nothing")}}, [])
    outcome = wizard.install_component(
        empty, wizard.Plan("linux", [], []), wizard.tool_components()[0]
    )
    assert outcome == "no_manager"


def test_install_scripts_are_shown_before_running(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    monkeypatch.setattr(wizard, "detect_os", lambda system=None: "linux")
    monkeypatch.setattr(wizard.tempfile, "tempdir", str(system["bin"].parent))  # type: ignore[union-attr]
    cursor = next(item for item in wizard.agent_components(config) if item.key == "cursor")
    declined = session_for(system, ["i", "n"])
    assert wizard.install_component(declined, wizard.Plan("linux", [], []), cursor) == "skipped"
    said = "\n".join(declined.said)  # type: ignore[attr-defined]
    assert "https://cursor.com/install" in said
    assert "echo installing" in said
    assert "SHA-256 of the script" in said
    assert not Path(str(system["log"])).exists()
    assert not list(system["bin"].parent.glob("timonel-installer-*"))  # type: ignore[union-attr]
    audit = [json.loads(line) for line in declined.audit_path.read_text().splitlines()]
    assert audit[-1]["action"] == "script-declined"
    accepted = session_for(system, ["i", "y"])
    wizard.install_component(accepted, wizard.Plan("linux", [], []), cursor)
    assert Path(str(system["log"])).read_text().startswith("sh ")


def test_login_runs_official_command_and_never_reads_tokens(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    codex = next(item for item in wizard.agent_components(config) if item.key == "codex")
    monkeypatch.setattr(wizard, "auth_state", lambda component, config, environ: "missing")
    session = session_for(system, ["l"])
    assert wizard.login_component(session, config, codex) == "login_unknown"
    assert Path(str(system["log"])).read_text().strip() == "codex login"
    said = "\n".join(session.said)  # type: ignore[attr-defined]
    assert "never sees your password" in said
    assert "browser window will open" in said


def test_api_keys_go_to_the_keyring(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(keys, "store", lambda name, value: stored.__setitem__(name, value))
    config = make_config()
    deepseek = next(item for item in wizard.agent_components(config) if item.key == "deepseek")
    session = session_for(system, ["k"])
    assert (
        wizard.store_key(session, deepseek, secret_input=lambda prompt: "sk-very-secret")
        == "key_stored"
    )
    assert stored == {"DEEPSEEK_API_KEY": "sk-very-secret"}
    assert "sk-very-secret" not in session.audit_path.read_text()
    assert "sk-very-secret" not in "\n".join(session.said)  # type: ignore[attr-defined]

    def broken(name: str, value: str) -> None:
        raise keys.KeyringUnavailable("none")

    monkeypatch.setattr(keys, "store", broken)
    assert (
        wizard.store_key(session_for(system, ["k"]), deepseek, secret_input=lambda prompt: "x")
        == "no_keyring"
    )


def test_student_pack_hint_and_terms(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    session = session_for(system, ["s"] * 10)
    wizard.run_wizard(config, session, ["copilot"], system="Linux")
    said = "\n".join(session.said)  # type: ignore[attr-defined]
    assert "https://github.com/settings/education/benefits" in said
    assert "read its terms" in said


def test_pick_install_prefers_official_managers() -> None:
    config = make_config()
    tools = {item.key: item for item in wizard.tool_components()}
    gh = tools["gh"]
    assert wizard.pick_install(gh, "windows", ["scoop", "winget"]).argv[:2] == ("winget", "install")  # type: ignore[union-attr]
    assert wizard.pick_install(gh, "linux", []).manual.startswith("https://github.com/cli/cli")  # type: ignore[union-attr]
    aider = next(item for item in wizard.agent_components(config) if item.key == "aider")
    assert wizard.pick_install(aider, "windows", ["uv", "pipx"]).manager == "pipx"  # type: ignore[union-attr]


def test_yes_without_allow_runs_nothing(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    fake_program(system["bin"], "brew", "")  # type: ignore[arg-type]
    session = session_for(system, [], assume_yes=True)
    results = wizard.run_wizard(config, session, ["codex", "claude"], system="Darwin")
    assert not Path(str(system["log"])).exists()
    assert set(results.values()) <= {"skipped", "ok"}


def test_script_install_never_runs_in_yes_mode(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    monkeypatch.setattr(wizard, "detect_os", lambda system=None: "linux")
    cursor = next(item for item in wizard.agent_components(config) if item.key == "cursor")
    session = session_for(system, [], assume_yes=True, allow=frozenset({"cursor"}))
    assert wizard.install_component(session, wizard.Plan("linux", [], []), cursor) == "skipped"
    assert not Path(str(system["log"])).exists()
    audit = [json.loads(line) for line in session.audit_path.read_text().splitlines()]
    assert audit[-1]["action"] == "script-declined"


def test_store_key_never_prompts_in_yes_or_dry_run(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(prompt: str) -> str:
        raise AssertionError("asked for a secret")

    def no_store(name: str, value: str) -> None:
        raise AssertionError("stored a key")

    monkeypatch.setattr(keys, "store", no_store)
    deepseek = next(
        item for item in wizard.agent_components(make_config()) if item.key == "deepseek"
    )
    for options in ({"assume_yes": True}, {"dry_run": True}):
        session = session_for(system, ["k"], **options)
        assert wizard.store_key(session, deepseek, secret_input=forbidden) == "skipped"


def test_keyring_errors_never_echo_the_secret(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    def leaky(name: str, value: str) -> None:
        raise keys.KeyringUnavailable(f"backend rejected {value}")

    monkeypatch.setattr(keys, "store", leaky)
    deepseek = next(
        item for item in wizard.agent_components(make_config()) if item.key == "deepseek"
    )
    session = session_for(system, ["k"])
    outcome = wizard.store_key(session, deepseek, secret_input=lambda prompt: "sk-leak-me-123")
    assert outcome == "no_keyring"
    assert "sk-leak-me-123" not in "\n".join(session.said)  # type: ignore[attr-defined]
    if session.audit_path.exists():
        assert "sk-leak-me-123" not in session.audit_path.read_text()


def test_long_install_scripts_say_how_much_was_not_shown(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_with_reachable_network(monkeypatch)
    monkeypatch.setattr(wizard, "detect_os", lambda system=None: "linux")
    cursor = next(item for item in wizard.agent_components(config) if item.key == "cursor")
    session = session_for(system, ["i", "n"])
    session.fetch = lambda url: "\n".join(f"echo {n}\x1b[2K" for n in range(100)).encode()
    wizard.install_component(session, wizard.Plan("linux", [], []), cursor)
    said = "\n".join(session.said)  # type: ignore[attr-defined]
    assert "60 more lines not shown" in said
    assert "\x1b" not in said


def test_single_letter_s_means_skip_never_yes() -> None:
    from timonel import i18n

    previous = i18n.current()
    try:
        for language in i18n.LANGUAGES:
            i18n.set_language(language)
            assert not wizard.yes("s")
            assert wizard.yes("yes")
    finally:
        i18n.set_language(previous)


def gh_component() -> wizard.Component:
    return next(item for item in wizard.tool_components() if item.key == "gh")


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (types.SimpleNamespace(timed_out=False, exit_code=0), "ok"),
        (types.SimpleNamespace(timed_out=False, exit_code=1), "missing"),
        (types.SimpleNamespace(timed_out=True, exit_code=-1), "unknown"),
        (None, "unknown"),
    ],
)
def test_gh_login_state_comes_from_gh_auth_status(
    monkeypatch: pytest.MonkeyPatch, outcome: object, expected: str
) -> None:
    asked: list[list[str]] = []

    def fake_quick(argv: list[str], env: object) -> object:
        asked.append(argv)
        return outcome

    monkeypatch.setattr(wizard, "quick", fake_quick)
    assert wizard.auth_state(gh_component(), make_config(), {}) == expected
    assert asked == [["gh", "auth", "status"]]


def test_login_is_skipped_when_gh_is_already_logged_in(
    system: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard, "auth_state", lambda component, config, environ: "ok")
    session = session_for(system, [])
    assert wizard.login_component(session, make_config(), gh_component()) == "ok"
    assert not Path(str(system["log"])).exists() or Path(str(system["log"])).read_text() == ""
