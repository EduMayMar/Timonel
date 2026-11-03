from timonel.doctor import diagnose, inspect_agent, parse_models

from .conftest import Fakes, make_config


def by_name(report):  # type: ignore[no-untyped-def]
    return {item.name: item for item in report}


def test_doctor_detects_fakes(fakes: Fakes) -> None:
    config = make_config()
    report = by_name(diagnose(config, environ=fakes.env))
    assert report["claude"].installed and report["claude"].auth == "ok"
    assert report["claude"].version == "claude 9.9.9 (fake)"
    assert report["codex"].auth == "ok"
    assert report["copilot"].auth == "ok"
    assert report["copilot"].auth_detail == "GH_TOKEN is set"
    assert report["deepseek"].auth == "ok"
    assert report["agy"].models == ("fake-pro", "fake-flash")
    assert report["claude"].models == ("fable", "opus", "sonnet")
    assert all(report[name].usable for name in ("claude", "codex", "copilot", "agy", "deepseek"))
    assert not report["gemini"].installed


def test_doctor_reports_missing_and_unauthenticated(fakes: Fakes) -> None:
    fakes.remove("agy")
    env = {**fakes.env, "FAKE_UNAUTH_CODEX": "1", "FAKE_UNAUTH_CLAUDE": "1"}
    env.pop("DEEPSEEK_API_KEY")
    env.pop("GH_TOKEN")
    report = by_name(diagnose(make_config(), environ=env))
    assert not report["agy"].installed
    assert report["codex"].auth == "missing"
    assert report["claude"].auth == "missing"
    assert report["deepseek"].auth == "missing"
    assert report["copilot"].auth == "unknown"
    assert not report["codex"].usable


def test_smoke_runs_a_real_prompt(fakes: Fakes) -> None:
    config = make_config()
    env = {**fakes.env, "FAKE_MODE": "success"}
    env_pong = {**env, "FAKE_MODE_CLAUDE": "success"}
    health = inspect_agent(config.agents["claude"], config, smoke=True, environ=env_pong)
    assert health.smoke_ok is False
    assert "reply='claude done'" in health.smoke
    broken = inspect_agent(
        config.agents["codex"], config, smoke=True, environ={**env, "FAKE_MODE_CODEX": "crash"}
    )
    assert broken.smoke_ok is False
    assert "exit 2" in broken.smoke


def test_smoke_pass_with_pong(fakes: Fakes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import timonel.doctor as doctor

    monkeypatch.setattr(doctor, "SMOKE_PROMPT", "[[say:pong]] Reply with exactly the word: pong")
    config = make_config()
    health = inspect_agent(config.agents["codex"], config, smoke=True, environ=fakes.env)
    assert health.smoke_ok is True
    agy = inspect_agent(
        config.agents["agy"], config, smoke=True, environ={**fakes.env, "FAKE_MODE_AGY": "need_tty"}
    )
    assert agy.smoke_ok is True


def test_parse_models_output() -> None:
    assert parse_models(
        "Available models:\n  * gemini-3-pro\n  - claude-sonnet-4-6 (default)\n\n"
    ) == (
        "gemini-3-pro",
        "claude-sonnet-4-6",
    )
