import importlib.util
import json
import sys
from pathlib import Path

import pytest

from .conftest import WINDOWS

SCRIPT = Path(__file__).parent.parent / "scripts" / "flag_drift.py"
spec = importlib.util.spec_from_file_location("flag_drift", SCRIPT)
assert spec is not None and spec.loader is not None
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)


def fake_cli(folder: Path, name: str, help_text: str) -> None:
    script = folder / f"{name}.py"
    script.write_text(f"print({help_text!r})\n", encoding="utf-8")
    if WINDOWS:
        (folder / f"{name}.bat").write_text(f'@"{sys.executable}" "{script}" %*\r\n')
    else:
        shim = folder / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        shim.chmod(0o755)


def test_reports_flags_missing_from_help(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "tool.yaml").write_text(
        "name: tool\ninstall: {drift: {npm: tool, help: [['run', '--help']]}}\n"
        "agent: {command: faketool, base_args: [run, --json, '--model={model}'], "
        "read_args: [--read-only], dangerous_args: [--yolo]}\n",
        encoding="utf-8",
    )
    (agents / "absent.yaml").write_text(
        "name: absent\ninstall: {drift: {npm: absent}}\nagent: {command: no-such-cli-xyz}\n",
        encoding="utf-8",
    )
    (agents / "nodrift.yaml").write_text("name: nodrift\nagent: {command: x}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli(bin_dir, "faketool", "Usage: faketool run [--json] [--model <m>] [--yolo-mode]")
    monkeypatch.setenv("PATH", str(bin_dir))
    report = drift.check(agents, 30)
    assert report["tool"] == {"missing": ["--read-only", "--yolo"]}
    assert "FileNotFoundError" in report["absent"]["error"]
    assert "nodrift" not in report
    out = tmp_path / "drift.md"
    assert drift.main(["--agents", str(agents), "--markdown", str(out)]) == 1
    text = out.read_text(encoding="utf-8")
    assert "`--read-only`" in text and "absent" not in text


def test_registry_connectors_with_drift_metadata_have_flags_to_check() -> None:
    import yaml

    folder = SCRIPT.parent.parent / "src" / "timonel" / "agents"
    for path in folder.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if (data.get("install") or {}).get("drift"):
            assert drift.connector_flags(data["agent"]), path.name
    assert json.dumps(drift.markdown({"x": {"error": "boom"}})).count("boom") == 1


def test_generated_docs_are_up_to_date() -> None:
    generator_spec = importlib.util.spec_from_file_location(
        "gen_docs", SCRIPT.parent / "gen_docs.py"
    )
    assert generator_spec is not None and generator_spec.loader is not None
    generator = importlib.util.module_from_spec(generator_spec)
    generator_spec.loader.exec_module(generator)
    assert generator.main(["--check"]) == 0
    configuration = (SCRIPT.parent.parent / "docs" / "reference" / "configuration.md").read_text(
        encoding="utf-8"
    )
    from timonel import schema

    for option in schema.options():
        assert f"`{option.key}`" in configuration


def test_every_recipe_validates() -> None:
    import re

    from timonel.config import validate_text

    text = (SCRIPT.parent.parent / "docs" / "recipes.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    assert len(blocks) >= 5
    for block in blocks:
        validate_text(block, "recipe.yaml")


def test_manifest_templates_fill_with_release_checksums(tmp_path: Path) -> None:
    fill_spec = importlib.util.spec_from_file_location(
        "fill_manifests", SCRIPT.parent / "fill_manifests.py"
    )
    assert fill_spec is not None and fill_spec.loader is not None
    filler = importlib.util.module_from_spec(fill_spec)
    fill_spec.loader.exec_module(filler)
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(
        "a" * 64
        + "  timonel-windows-x64.exe\n"
        + "b" * 64
        + "  timonel-macos-arm64\n"
        + "c" * 64
        + " *timonel-linux-x64\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    assert filler.main(["1.2.3", str(sums), "--out", str(out)]) == 0
    scoop = json.loads((out / "scoop" / "timonel.json").read_text(encoding="utf-8"))
    assert scoop["version"] == "1.2.3"
    assert scoop["architecture"]["64bit"]["hash"] == "a" * 64
    assert "$version" in scoop["autoupdate"]["architecture"]["64bit"]["url"]
    winget = (out / "winget" / "EduMayMar.Timonel.installer.yaml").read_text(encoding="utf-8")
    assert "v1.2.3/timonel-windows-x64.exe" in winget and "a" * 64 in winget
    formula = (out / "homebrew" / "timonel.rb").read_text(encoding="utf-8")
    assert "b" * 64 in formula and "c" * 64 in formula and "#{bin}" in formula
    sums.write_text("a" * 64 + "  timonel-windows-x64.exe\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        filler.main(["1.2.3", str(sums), "--out", str(out)])


@pytest.mark.skipif(WINDOWS, reason="POSIX shell installer")
def test_shell_installer_shows_the_command_and_asks(tmp_path: Path) -> None:
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    uv = bin_dir / "uv"
    uv.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n', encoding="utf-8")
    uv.chmod(0o755)
    script = SCRIPT.parent.parent / "install" / "install.sh"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    declined = subprocess.run(
        ["sh", str(script)],
        input="n\n",
        capture_output=True,
        text=True,
        env=env,
        start_new_session=True,
    )
    assert "uv tool install timonel" in declined.stdout
    assert "Nothing was changed." in declined.stdout
    assert not log.exists()
    accepted = subprocess.run(
        ["sh", str(script)],
        input="y\n",
        capture_output=True,
        text=True,
        env=env,
        start_new_session=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert log.read_text(encoding="utf-8").strip() == "tool install timonel"
    assert "sudo" not in script.read_text(encoding="utf-8").replace("never uses sudo", "")
