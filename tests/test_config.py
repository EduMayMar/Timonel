from pathlib import Path

import pytest

from timonel.config import ConfigError, is_dangerous, load_config, user_config_path, validate_text


def problems(text: str) -> list[str]:
    with pytest.raises(ConfigError) as caught:
        validate_text(text, "routes.yaml")
    return caught.value.problems


def test_defaults_are_valid() -> None:
    config = validate_text("")
    assert {"claude", "deepseek", "codex", "copilot", "agy", "gemini", "aider", "ollama"} <= set(
        config.agents
    )
    assert config.agents["openai"].kind == "provider"
    assert config.agents["gemini"].enabled is False
    assert config.agents["claude"].meta["verification"]["status"] == "verified"
    assert sorted(config.tiers) == [1, 2, 3, 4]
    assert {"cheap", "balanced", "max"} <= set(config.profiles)
    assert config.agents["agy"].pty is True
    assert (
        config.agents["deepseek"].env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    )
    assert config.agents["deepseek"].env_from == {"ANTHROPIC_AUTH_TOKEN": "DEEPSEEK_API_KEY"}


def test_user_override_merges_agent_fields_and_replaces_tiers() -> None:
    config = validate_text(
        "agents:\n  claude:\n    billing: api\ntiers:\n  1:\n    - {agent: codex, model: o3}\n"
    )
    assert config.agents["claude"].billing == "api"
    assert config.agents["claude"].command == "claude"
    assert list(config.tiers) == [1]
    assert config.tiers[1][0].model == "o3"


def test_errors_point_to_line_and_field() -> None:
    found = problems("agents:\n  claude:\n    billing: free\n")
    assert found == [
        "routes.yaml:3: agents.claude.billing: expected one of subscription, api, got 'free'"
    ]


def test_unknown_agent_in_tier_is_reported_with_line() -> None:
    found = problems("tiers:\n  2:\n    - {agent: codexx}\n  1:\n    - {agent: codex}\n")
    assert len(found) == 1
    assert found[0].startswith(
        "routes.yaml:3: tiers.2.0.agent: unknown agent 'codexx'; did you mean 'codex'?"
    )


def test_providers_cannot_edit_code() -> None:
    found = problems("tiers:\n  1:\n    - {agent: openai, model: x}\n")
    assert "is an API provider" in found[0]
    assert (
        validate_text("classifier:\n  ladder:\n    - {agent: openai, model: x}\n")
        .classifier.ladder[0]
        .agent
        == "openai"
    )


def test_unknown_keys_suggest_the_closest_option() -> None:
    found = problems("escalaton:\n  max_escalations: 1\nexecution:\n  total_timeout: 5\n")
    assert "routes.yaml:1: escalaton: unknown field; did you mean 'escalation'?" in found
    assert (
        "routes.yaml:4: execution.total_timeout: unknown field; did you mean 'total_timeout_s'?"
        in found
    )


def test_unknown_placeholder_and_dangerous_flag_are_rejected() -> None:
    found = problems(
        "agents:\n  claude:\n    base_args: ['-p', '{secret}', '--dangerously-skip-permissions']\n"
    )
    assert any("unknown placeholder {secret}" in item for item in found)
    assert any("move it to dangerous_args" in item for item in found)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("--dangerously-skip-permissions", True),
        ("--yolo", True),
        ("--allow-all", True),
        ("danger-full-access", True),
        ("bypassPermissions", True),
        ("--allow-all-tools", False),
        ("--permission-mode", False),
    ],
)
def test_dangerous_detection(token: str, expected: bool) -> None:
    assert is_dangerous(token) is expected


def test_prompt_placement_must_be_consistent() -> None:
    assert any(
        "no token contains {prompt}" in item
        for item in problems("agents:\n  copilot:\n    prompt_args: ['-p']\n")
    )
    assert any(
        "{prompt} must not appear" in item
        for item in problems("agents:\n  claude:\n    prompt_args: ['{prompt}']\n")
    )


def test_type_errors_and_ranges() -> None:
    found = problems(
        "execution:\n  max_escalations: 99\n  verify: [[]]\n"
        "profiles:\n  odd: {start_offset: x, skip_tiers: [9], wat: 1}\n"
        "adaptive: {enabled: maybe}\n"
        "rate_limit_patterns: ['(']\n"
        "tiers:\n  7: []\n"
        "bogus: 1\n"
    )
    joined = "\n".join(found)
    assert "routes.yaml:2: escalation.max_escalations: must be at most 10, got 99" in joined
    assert "routes.yaml:3: verification.commands.0: needs at least 1 items" in joined
    assert "profiles.odd.start_offset: expected integer" in joined
    assert "profiles.odd.skip_tiers.0: must be at most 4, got 9" in joined
    assert "profiles.odd.wat: unknown field" in joined
    assert "adaptive.enabled: expected boolean" in joined
    assert "rate_limit_patterns.0: invalid regular expression" in joined
    assert "tiers.7: expected one of 1, 2, 3, 4" in joined
    assert "bogus: unknown field" in joined


def test_agent_field_validation() -> None:
    found = problems(
        "agents:\n  new:\n    billing: api\n  other:\n    command: x\n    env: {'1BAD': y}\n"
        "    env_from: {TOKEN: 'not a name'}\n    cost_per_attempt_usd: -1\n    color: red\n"
        "    known_models: oops\n"
    )
    joined = "\n".join(found)
    assert "agents.other.env.1BAD: does not match the expected format" in joined
    assert "agents.other.env_from.TOKEN: does not match the expected format" in joined
    assert "agents.other.cost_per_attempt_usd: must be at least 0, got -1" in joined
    assert "agents.other.color: unknown field" in joined
    assert "agents.other.known_models: expected array" in joined
    assert "agents.new.command: is required" in "\n".join(
        problems("agents:\n  new:\n    billing: api\n")
    )


def test_invalid_yaml_and_non_mapping() -> None:
    assert problems("tiers: [\n")[0].startswith("routes.yaml:")
    assert problems("- just\n- a list\n") == ["routes.yaml:1: top level must be a mapping"]


def test_empty_tiers_rejected() -> None:
    assert any("at least one tier" in item for item in problems("tiers: {}\n"))


def test_version_must_be_one() -> None:
    assert any("version: expected one of 1, got 2" in item for item in problems("version: 2\n"))


def test_load_config_from_user_dir_and_explicit_path(home: Path, tmp_path: Path) -> None:
    assert load_config().source is None
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("execution: {max_escalations: 1}\n", encoding="utf-8")
    assert load_config().escalation.max_escalations == 1
    explicit = tmp_path / "custom.yaml"
    explicit.write_text("execution: {max_escalations: 0}\n", encoding="utf-8")
    assert load_config(explicit).source == explicit
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_user_dir_platform_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIMONEL_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/cfg")
    if __import__("os").name != "nt":
        assert user_config_path() == Path("/cfg/timonel/routes.yaml")


def test_cross_field_rules() -> None:
    found = "\n".join(
        problems(
            "agents:\n  agy:\n    prompt_via: stdin\n    prompt_args: []\n"
            "profiles:\n  odd: {min_tier: 4, max_tier: 2}\n"
        )
    )
    assert "agents.agy.pty: a pseudo-terminal agent needs prompt_via: arg" in found
    assert "profiles.odd.min_tier: min_tier 4 is above max_tier 2" in found
    schema_found = "\n".join(problems("execution:\n  verify_env: ['1BAD']\n  colour: x\n"))
    assert "verification.env_allow.0: does not match the expected format" in schema_found
    assert "execution.colour: unknown field" in schema_found


def test_copilot_never_gets_shell_by_default() -> None:
    copilot = validate_text("").agents["copilot"]
    assert "--deny-tool=shell" in copilot.write_args
    assert "--deny-tool=shell" in copilot.read_args
