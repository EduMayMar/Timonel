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
