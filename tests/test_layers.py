import json
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from timonel import schema
from timonel.config import (
    ConfigError,
    load_config,
    project_commands,
    revoke_project,
    trust_project,
    user_agents_dir,
    user_config_path,
    validate_text,
)


def write_user(text: str) -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_layer_precedence_and_origins(home: Path, tmp_path: Path) -> None:
    write_user("execution:\n  total_timeout_s: 100\n  idle_timeout_s: 50\nmode: expert\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".timonel.yaml").write_text("execution:\n  idle_timeout_s: 40\n", encoding="utf-8")
    trust_project(repo / ".timonel.yaml")
    env = {
        "TIMONEL_EXECUTION__TOTAL_TIMEOUT_S": "70",
        "TIMONEL_HOME": str(home),
        "TIMONEL_NOT_A_KEY": "x",
    }
    config = load_config(
        repo=repo, environ=env, overrides=["execution.orphan_grace_s=1.5", "language=pt"]
    )
    assert config.execution.total_timeout_s == 70
    assert config.execution.idle_timeout_s == 40
    assert config.execution.orphan_grace_s == 1.5
    assert config.mode == "expert"
    assert config.language == "pt"
    assert config.origins["execution.total_timeout_s"] == "env"
    assert config.origins["execution.idle_timeout_s"] == "project"
    assert config.origins["execution.orphan_grace_s"] == "cli"
    assert config.origins["mode"] == "user"
    assert config.origins["escalation.max_escalations"] == "default"
    assert config.project.trusted


def test_untrusted_or_changed_project_file_is_ignored(home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project = repo / ".timonel.yaml"
    project.write_text("verification:\n  commands: [[rm, -rf, x]]\n", encoding="utf-8")
    config = load_config(repo=repo, environ={})
    assert config.verification.commands == ()
    assert config.project.path == project and not config.project.trusted
    assert project_commands(yaml.safe_load(project.read_text())) == ["verify: rm -rf x"]
    trust_project(project)
    assert load_config(repo=repo, environ={}).verification.commands == (("rm", "-rf", "x"),)
    project.write_text("verification:\n  commands: [[curl, evil]]\n", encoding="utf-8")
    changed = load_config(repo=repo, environ={})
    assert changed.verification.commands == ()
    assert not changed.project.trusted
    assert revoke_project(project)
    assert not revoke_project(project)


def test_project_commands_summary() -> None:
    data = {
        "hooks": {"pre_run": [["make", "lint"]]},
        "agents": {"claude": {"base_args": ["-p"]}, "codex": {"billing": "api"}},
        "routing": {"rules": [{"then": {"verify": [["npm", "test"]]}}]},
    }
    assert project_commands(data) == [
        "hook pre_run: make lint",
        "agent claude: changes how the agent is launched",
        "rule 0 verify: npm test",
    ]


def test_cli_override_errors_and_env_values(home: Path) -> None:
    with pytest.raises(ConfigError, match="use key=value"):
        load_config(overrides=["nonsense"], environ={})
    with pytest.raises(ConfigError, match="execution.total_timeout_s: expected integer"):
        load_config(overrides=["execution.total_timeout_s=soon"], environ={})
    config = load_config(environ={"TIMONEL_ROUTING__DENY_AGENTS": "[codex, agy]"})
    assert config.routing.deny_agents == ("codex", "agy")


def test_legacy_keys_are_migrated() -> None:
    config = validate_text(
        "execution:\n  verify: [[pytest]]\n  review_min_tier: 2\n  max_escalations: 1\n"
        "classifier:\n  - {agent: claude}\nreviewer:\n  - {agent: codex}\n"
    )
    assert config.verification.commands == (("pytest",),)
    assert config.review.min_tier == 2
    assert config.escalation.max_escalations == 1
    assert config.classifier.ladder[0].agent == "claude"
    assert config.review.reviewers[0].agent == "codex"


def test_user_connectors_extend_the_registry(home: Path) -> None:
    folder = user_agents_dir()
    folder.mkdir(parents=True)
    (folder / "mine.yaml").write_text(
        "name: mine\nkind: agent\ntitle: My CLI\nverification: {status: unverified}\n"
        "agent:\n  command: mine\n  prompt_via: stdin\n",
        encoding="utf-8",
    )
    config = load_config(environ={})
    assert config.agents["mine"].meta["title"] == "My CLI"
    assert config.agents["mine"].verified is False
    (folder / "broken.yaml").write_text("name: Bad Name\ncolor: red\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(environ={})
    assert any("unknown connector field 'color'" in item for item in caught.value.problems)
    assert any("agent section is required" in item for item in caught.value.problems)


def test_every_registry_connector_is_consistent() -> None:
    config = validate_text("")
    for name, agent in config.agents.items():
        meta = agent.meta
        assert meta["verification"]["status"] in ("verified", "docs", "unverified"), name
        assert meta["verification"].get("date"), name
        assert meta.get("title") and meta.get("homepage", "").startswith("http"), name
        if agent.kind == "provider":
            assert agent.provider is not None and agent.provider.base_url.startswith("http"), name
        install = meta.get("install", {})
        for manager, command in install.items():
            if manager in ("npm", "pnpm", "pipx", "uv", "brew", "winget", "scoop", "choco"):
                assert isinstance(command, list) and command[0] in (manager, "uv"), (name, manager)


def test_schema_json_and_defaults_are_consistent() -> None:
    loaded = json.loads(schema.schema_json())
    assert loaded["$schema"].startswith("https://json-schema.org/")
    assert schema.validate(schema.defaults()) == []
    for option in schema.options():
        assert option.node.get("description"), option.key
        assert option.description("es") and option.description("pt"), option.key


def test_published_schema_file_is_up_to_date() -> None:
    published = Path(__file__).parent.parent / "schema" / "timonel.schema.json"
    assert published.read_text(encoding="utf-8") == schema.schema_json()


SCALAR_OPTIONS = [
    option
    for option in schema.options()
    if option.node.get("type") in ("integer", "boolean", "string", "number")
]


@settings(max_examples=300, deadline=None)
@given(
    st.sampled_from(SCALAR_OPTIONS),
    st.one_of(
        st.integers(-100000, 100000),
        st.booleans(),
        st.text(max_size=20),
        st.floats(allow_nan=False, allow_infinity=False),
    ),
)
def test_scalar_values_validate_exactly_when_schema_allows(
    option: schema.Option, value: object
) -> None:
    data: dict[str, object] = {}
    target = data
    for part in option.path[:-1]:
        target = target.setdefault(part, {})  # type: ignore[assignment]
    target[option.path[-1]] = value
    problems = schema.validate(data)
    expected_type = option.node["type"]
    type_ok = schema.TYPE_CHECKS[expected_type](value) if isinstance(expected_type, str) else True
    if not type_ok:
        assert problems
    if not problems:
        assert type_ok


@settings(max_examples=100, deadline=None)
@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12))
def test_unknown_top_level_keys_are_always_rejected(key: str) -> None:
    if key in schema.load_schema()["properties"]:
        return
    problems = schema.validate({key: 1})
    assert problems and problems[0].message.startswith("unknown field")
