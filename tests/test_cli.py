import json
from pathlib import Path

import pytest

from timonel.cli import main, split_command, suggest_verify
from timonel.config import user_config_path

from .conftest import TEST_ROUTES, Fakes, run_git

SIMPLE = json.dumps(
    {"complexity": 1, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
)


@pytest.fixture
def cli_env(fakes: Fakes, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for key, value in fakes.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FAKE_CLASSIFICATION", SIMPLE)
    import timonel.cli as cli

    monkeypatch.setattr(cli.console, "width", 240)
    config = tmp_path / "routes.yaml"
    config.write_text(TEST_ROUTES, encoding="utf-8")
    return config


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_doctor_and_models(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(capsys, "--config", str(cli_env), "doctor")
    assert code == 0
    assert "claude 9.9.9 (fake)" in out
    code, out = run_cli(capsys, "--config", str(cli_env), "doctor", "--json")
    assert {item["name"] for item in json.loads(out)} >= {"claude", "agy"}
    code, out = run_cli(capsys, "--config", str(cli_env), "models", "agy", "--json")
    assert json.loads(out) == {"agy": ["fake-pro", "fake-flash"]}
    code, out = run_cli(capsys, "--config", str(cli_env), "models")
    assert "fake-pro" in out
    assert run_cli(capsys, "--config", str(cli_env), "models", "ghost")[0] == 2


def test_doctor_smoke_fails_without_pong(cli_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(capsys, "--config", str(cli_env), "doctor", "--smoke")
    assert code == 1
    assert "fail" in out


def test_plan_run_status_diff_apply(
    cli_env: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run_cli(
        capsys, "--config", str(cli_env), "plan", "fix typo in README.md", "--repo", str(repo)
    )
    assert code == 0
    assert "classification" in out and "copilot" in out
    code, out = run_cli(
        capsys,
        "--config",
        str(cli_env),
        "plan",
        "x",
        "--repo",
        str(repo),
        "--json",
        "--model-classify",
    )
    assert json.loads(out)["classification"]["source"] == "model"
    code, out = run_cli(
        capsys,
        "--config",
        str(cli_env),
        "run",
        "[[edit all:result.txt=good]] add file",
        "--repo",
        str(repo),
        "--verify",
        "git status",
    )
    assert code == 0, out
    assert "succeeded" in out
    run_id = out.split("run ")[1].split()[0]
    code, out = run_cli(capsys, "--config", str(cli_env), "status")
    assert run_id in out
    code, out = run_cli(capsys, "--config", str(cli_env), "status", run_id)
    assert "attempts" in out
    code, out = run_cli(capsys, "--config", str(cli_env), "status", run_id, "--json")
    assert json.loads(out)["id"] == run_id
    code, out = run_cli(capsys, "--config", str(cli_env), "diff", run_id)
    assert "+good" in out
    code, out = run_cli(capsys, "--config", str(cli_env), "apply", run_id)
    assert code == 0
    assert (repo / "result.txt").read_text() == "good"
    code, out = run_cli(capsys, "--config", str(cli_env), "status", "--json")
    assert json.loads(out)[0]["status"] == "applied"
    code, out = run_cli(capsys, "--config", str(cli_env), "stats")
    assert "by agent" in out
    code, out = run_cli(capsys, "--config", str(cli_env), "stats", "--json")
    assert json.loads(out)["runs"] == 1


def test_run_json_failure_and_discard(
    cli_env: Path, repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "crash")
    code, out = run_cli(
        capsys,
        "--config",
        str(cli_env),
        "run",
        "go",
        "--repo",
        str(repo),
        "--json",
        "--force",
        "codex",
    )
    assert code == 1
    data = json.loads(out)
    assert data["status"] == "failed"
    code, out = run_cli(capsys, "--config", str(cli_env), "discard", data["id"])
    assert code == 0
    code, out = run_cli(capsys, "--config", str(cli_env), "apply", data["id"])
    assert code == 1
    assert "only succeeded" in out
    assert run_cli(capsys, "--config", str(cli_env), "resume", data["id"])[0] == 1


def test_dangerous_requires_opt_in_and_confirmation(
    cli_env: Path, repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out = run_cli(
        capsys, "--config", str(cli_env), "run", "x", "--repo", str(repo), "--dangerous"
    )
    assert code == 2 and "allow_dangerous" in out
    opted = tmp_path / "danger.yaml"
    opted.write_text(
        TEST_ROUTES + "agents:\n  codex:\n    allow_dangerous: true\n", encoding="utf-8"
    )
    code, out = run_cli(
        capsys, "--config", str(opted), "run", "x", "--repo", str(repo), "--dangerous"
    )
    assert code == 2 and "explicit confirmation" in out


def test_invalid_config_reports_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], home: Path
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("tiers:\n  1:\n    - {agent: nobody}\n", encoding="utf-8")
    code, out = run_cli(capsys, "--config", str(bad), "doctor")
    assert code == 2
    assert f"{bad}:3: tiers.1.0.agent" in out
    good = tmp_path / "good.yaml"
    good.write_text("execution: {max_escalations: 1}\n", encoding="utf-8")
    assert run_cli(capsys, "--config", str(good), "validate")[0] == 0
    assert run_cli(capsys, "validate")[0] == 0
    assert run_cli(capsys, "validate", str(bad))[0] == 2


def test_init_writes_filtered_routes(
    cli_env: Path,
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    fakes: Fakes,
) -> None:
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    fakes.remove("agy")
    code, out = run_cli(capsys, "--config", str(cli_env), "init", "--yes")
    assert code == 0, out
    written = user_config_path().read_text(encoding="utf-8")
    assert "agy" not in written
    assert "pytest" in written
    assert run_cli(capsys, "--config", str(cli_env), "init")[0] == 2
    assert run_cli(capsys, "--config", str(cli_env), "init", "--force", "--yes")[0] == 0


def test_errors_are_friendly(
    cli_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run_cli(capsys, "--config", str(cli_env), "run", "x", "--repo", str(tmp_path))
    assert code == 1 and "not inside a git repository" in out
    assert "needs a git repository" in out and "/git-init" in out
    code, out = run_cli(
        capsys, "--config", str(cli_env), "plan", "explain x", "--repo", str(tmp_path)
    )
    assert code == 0 and "route" in out
    code, out = run_cli(capsys, "--config", str(cli_env), "status", "missing")
    assert code == 1 and "unknown run" in out


def test_helpers(tmp_path: Path) -> None:
    assert (
        split_command("pytest -q 'tests/a b'") == ("pytest", "-q", "tests/a b")
        or __import__("os").name == "nt"
    )
    assert suggest_verify(tmp_path) is None
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert suggest_verify(tmp_path) == ["go", "test", "./..."]


def test_module_entry_point(cli_env: Path) -> None:
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "timonel", "--version"], capture_output=True, text=True, check=True
    )
    assert completed.stdout.startswith("timonel ")
    assert run_git(Path.cwd(), "--version").startswith("git")
