from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

import yaml

Billing = Literal["subscription", "api"]
ParserKind = Literal["text", "json", "stream-json", "jsonl"]
PromptVia = Literal["arg", "stdin"]

PLACEHOLDERS = frozenset({"prompt", "model", "effort", "timeout_s"})
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
DANGEROUS_MARKERS = (
    "--dangerously",
    "--yolo",
    "danger-full-access",
    "bypasspermissions",
    "approval_policy=never",
    "--allow-tool=shell",
)
DANGEROUS_EXACT = frozenset({"--allow-all", "--allow-all-paths", "--allow-all-urls"})
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TIERS = (1, 2, 3, 4)
EXECUTION_FIELDS = frozenset(
    {
        "max_escalations",
        "review_min_tier",
        "cooldown_minutes",
        "total_timeout_s",
        "idle_timeout_s",
        "verify",
        "verify_timeout_s",
        "verify_env",
    }
)


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Detect:
    version: tuple[str, ...] = ("--version",)
    auth: tuple[str, ...] = ()
    auth_ok_pattern: str = ""
    auth_env: tuple[str, ...] = ()


@dataclass(frozen=True)
class Agent:
    name: str
    command: str
    billing: Billing
    parser: ParserKind
    verified: bool = False
    prompt_via: PromptVia = "arg"
    pty: bool = False
    base_args: tuple[str, ...] = ()
    read_args: tuple[str, ...] = ()
    write_args: tuple[str, ...] = ()
    model_args: tuple[str, ...] = ()
    effort_args: tuple[str, ...] = ()
    prompt_args: tuple[str, ...] = ()
    dangerous_args: tuple[str, ...] = ()
    allow_dangerous: bool = False
    env: dict[str, str] = field(default_factory=dict)
    env_from: dict[str, str] = field(default_factory=dict)
    env_remove: tuple[str, ...] = ()
    models_command: tuple[str, ...] = ()
    known_models: tuple[str, ...] = ()
    rate_limit_patterns: tuple[str, ...] = ()
    cost_per_attempt_usd: float | None = None
    detect: Detect = field(default_factory=Detect)


@dataclass(frozen=True)
class Rung:
    agent: str
    model: str = ""
    effort: str = ""

    @property
    def label(self) -> str:
        return f"{self.agent}:{self.model or 'default'}"


@dataclass(frozen=True)
class Profile:
    name: str
    start_offset: int = 0
    min_tier: int = 1
    max_tier: int = 4
    skip_tiers: frozenset[int] = frozenset()
    max_escalations: int | None = None


@dataclass(frozen=True)
class Execution:
    max_escalations: int = 2
    review_min_tier: int = 3
    cooldown_minutes: int = 30
    total_timeout_s: int = 1800
    idle_timeout_s: int = 300
    verify: tuple[tuple[str, ...], ...] = ()
    verify_timeout_s: int = 900
    verify_env: tuple[str, ...] = ()


@dataclass(frozen=True)
class Adaptive:
    enabled: bool = True
    min_samples: int = 5


@dataclass(frozen=True)
class Decompose:
    auto: bool = False
    max_subtasks: int = 6
    max_parallel: int = 3


@dataclass(frozen=True)
class Config:
    agents: dict[str, Agent]
    tiers: dict[int, tuple[Rung, ...]]
    profiles: dict[str, Profile]
    classifier: tuple[Rung, ...]
    planner: tuple[Rung, ...]
    reviewer: tuple[Rung, ...]
    rate_limit_patterns: tuple[str, ...]
    execution: Execution
    adaptive: Adaptive
    decompose: Decompose
    source: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)


Mark = tuple[str, int]
Marks = dict[tuple[str, ...], Mark]


def default_text() -> str:
    return resources.files("timonel").joinpath("default_routes.yaml").read_text(encoding="utf-8")


def user_config_dir() -> Path:
    if override := os.environ.get("TIMONEL_HOME"):
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "timonel"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "timonel"


def user_config_path() -> Path:
    return user_config_dir() / "routes.yaml"


def collect_marks(node: yaml.Node, filename: str, path: tuple[str, ...], marks: Marks) -> None:
    marks[path] = (filename, node.start_mark.line + 1)
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            collect_marks(value_node, filename, (*path, str(key_node.value)), marks)
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            collect_marks(item, filename, (*path, str(index)), marks)


def parse_yaml(text: str, filename: str) -> tuple[dict[str, Any], Marks]:
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        line = getattr(getattr(error, "problem_mark", None), "line", -1) + 1
        raise ConfigError([f"{filename}:{line}: invalid YAML: {error}"]) from error
    marks: Marks = {}
    if node is not None:
        collect_marks(node, filename, (), marks)
    if data is None:
        return {}, marks
    if not isinstance(data, dict):
        raise ConfigError([f"{filename}:1: top level must be a mapping"])
    return data, marks


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and key != "tiers":
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Validator:
    def __init__(self, marks: Marks) -> None:
        self.marks = marks
        self.problems: list[str] = []

    def where(self, path: tuple[str, ...]) -> str:
        probe = path
        while probe and probe not in self.marks:
            probe = probe[:-1]
        filename, line = self.marks.get(probe, ("routes.yaml", 0))
        return f"{filename}:{line}"

    def fail(self, path: tuple[str, ...], message: str) -> None:
        self.problems.append(f"{self.where(path)}: {'.'.join(path) or '<root>'}: {message}")

    def mapping(self, value: Any, path: tuple[str, ...]) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.fail(path, f"expected a mapping, got {type(value).__name__}")
            return {}
        return value

    def string(self, value: Any, path: tuple[str, ...], default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            self.fail(path, f"expected a string, got {type(value).__name__}")
            return default
        return str(value)

    def boolean(self, value: Any, path: tuple[str, ...], default: bool) -> bool:
        if value is None:
            return default
        if not isinstance(value, bool):
            self.fail(path, f"expected true or false, got {value!r}")
            return default
        return value

    def integer(self, value: Any, path: tuple[str, ...], default: int, low: int, high: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            self.fail(path, f"expected an integer, got {value!r}")
            return default
        if not low <= value <= high:
            self.fail(path, f"must be between {low} and {high}, got {value}")
            return default
        return value

    def number(self, value: Any, path: tuple[str, ...]) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            self.fail(path, f"expected a non-negative number, got {value!r}")
            return None
        return float(value)

    def choice(
        self, value: Any, path: tuple[str, ...], options: tuple[str, ...], default: str
    ) -> str:
        if value is None:
            return default
        if value not in options:
            self.fail(path, f"expected one of {', '.join(options)}, got {value!r}")
            return default
        return str(value)

    def strings(self, value: Any, path: tuple[str, ...]) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            self.fail(path, f"expected a list of strings, got {type(value).__name__}")
            return ()
        result = []
        for index, item in enumerate(value):
            if isinstance(item, bool) or not isinstance(item, str | int | float):
                self.fail((*path, str(index)), f"expected a string, got {item!r}")
            else:
                result.append(str(item))
        return tuple(result)

    def template(self, value: Any, path: tuple[str, ...]) -> tuple[str, ...]:
        tokens = self.strings(value, path)
        for index, token in enumerate(tokens):
            for name in PLACEHOLDER_RE.findall(token):
                if name not in PLACEHOLDERS:
                    allowed = ", ".join(sorted(PLACEHOLDERS))
                    self.fail((*path, str(index)), f"unknown placeholder {{{name}}}; use {allowed}")
            if is_dangerous(token):
                self.fail(
                    (*path, str(index)), f"{token!r} bypasses safety; move it to dangerous_args"
                )
        return tokens

    def env_map(self, value: Any, path: tuple[str, ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, item in self.mapping(value, path).items():
            if not ENV_NAME_RE.match(str(key)):
                self.fail((*path, str(key)), "invalid environment variable name")
                continue
            result[str(key)] = self.string(item, (*path, str(key)))
        return result

    def regexes(self, value: Any, path: tuple[str, ...]) -> tuple[str, ...]:
        patterns = self.strings(value, path)
        for index, pattern in enumerate(patterns):
            try:
                re.compile(pattern)
            except re.error as error:
                self.fail((*path, str(index)), f"invalid regular expression: {error}")
        return patterns


def is_dangerous(token: str) -> bool:
    lowered = token.lower()
    return token in DANGEROUS_EXACT or any(marker in lowered for marker in DANGEROUS_MARKERS)


def build_agent(name: str, raw: Any, check: Validator) -> Agent | None:
    path = ("agents", name)
    data = check.mapping(raw, path)
    command = check.string(data.get("command"), (*path, "command"))
    if not command:
        check.fail((*path, "command"), "is required")
        return None
    known = {
        "command",
        "billing",
        "parser",
        "verified",
        "prompt_via",
        "pty",
        "base_args",
        "read_args",
        "write_args",
        "model_args",
        "effort_args",
        "prompt_args",
        "dangerous_args",
        "allow_dangerous",
        "env",
        "env_from",
        "env_remove",
        "models_command",
        "known_models",
        "rate_limit_patterns",
        "cost_per_attempt_usd",
        "detect",
    }
    for key in data:
        if key not in known:
            check.fail((*path, str(key)), "unknown field")
    detect_raw = check.mapping(data.get("detect"), (*path, "detect"))
    detect = Detect(
        version=check.strings(detect_raw.get("version"), (*path, "detect", "version"))
        or ("--version",),
        auth=check.strings(detect_raw.get("auth"), (*path, "detect", "auth")),
        auth_ok_pattern=check.string(
            detect_raw.get("auth_ok_pattern"), (*path, "detect", "auth_ok_pattern")
        ),
        auth_env=check.strings(detect_raw.get("auth_env"), (*path, "detect", "auth_env")),
    )
    env_from = check.env_map(data.get("env_from"), (*path, "env_from"))
    for target, source in env_from.items():
        if not ENV_NAME_RE.match(source):
            check.fail((*path, "env_from", target), "source must be an environment variable name")
    prompt_args = check.template(data.get("prompt_args"), (*path, "prompt_args"))
    prompt_via = cast(
        PromptVia,
        check.choice(data.get("prompt_via"), (*path, "prompt_via"), ("arg", "stdin"), "arg"),
    )
    if prompt_via == "arg" and not any("{prompt}" in token for token in prompt_args):
        check.fail((*path, "prompt_args"), "prompt_via is 'arg' but no token contains {prompt}")
    if prompt_via == "stdin" and any("{prompt}" in token for token in prompt_args):
        check.fail((*path, "prompt_args"), "prompt_via is 'stdin' so {prompt} must not appear")
    pty = check.boolean(data.get("pty"), (*path, "pty"), False)
    if pty and prompt_via == "stdin":
        check.fail((*path, "pty"), "a pseudo-terminal agent needs prompt_via: arg")
    return Agent(
        name=name,
        command=command,
        billing=cast(
            Billing,
            check.choice(data.get("billing"), (*path, "billing"), ("subscription", "api"), "api"),
        ),
        parser=cast(
            ParserKind,
            check.choice(
                data.get("parser"),
                (*path, "parser"),
                ("text", "json", "stream-json", "jsonl"),
                "text",
            ),
        ),
        verified=check.boolean(data.get("verified"), (*path, "verified"), False),
        prompt_via=prompt_via,
        pty=pty,
        base_args=check.template(data.get("base_args"), (*path, "base_args")),
        read_args=check.template(data.get("read_args"), (*path, "read_args")),
        write_args=check.template(data.get("write_args"), (*path, "write_args")),
        model_args=check.template(data.get("model_args"), (*path, "model_args")),
        effort_args=check.template(data.get("effort_args"), (*path, "effort_args")),
        prompt_args=prompt_args,
        dangerous_args=check.strings(data.get("dangerous_args"), (*path, "dangerous_args")),
        allow_dangerous=check.boolean(
            data.get("allow_dangerous"), (*path, "allow_dangerous"), False
        ),
        env=check.env_map(data.get("env"), (*path, "env")),
        env_from=env_from,
        env_remove=check.strings(data.get("env_remove"), (*path, "env_remove")),
        models_command=check.strings(data.get("models_command"), (*path, "models_command")),
        known_models=check.strings(data.get("known_models"), (*path, "known_models")),
        rate_limit_patterns=check.regexes(
            data.get("rate_limit_patterns"), (*path, "rate_limit_patterns")
        ),
        cost_per_attempt_usd=check.number(
            data.get("cost_per_attempt_usd"), (*path, "cost_per_attempt_usd")
        ),
        detect=detect,
    )


def build_ladder(
    raw: Any, path: tuple[str, ...], agents: dict[str, Any], check: Validator
) -> tuple[Rung, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        check.fail(path, "expected a list of {agent, model, effort}")
        return ()
    rungs = []
    for index, item in enumerate(raw):
        item_path = (*path, str(index))
        data = check.mapping(item, item_path)
        agent = check.string(data.get("agent"), (*item_path, "agent"))
        if agent not in agents:
            check.fail(
                (*item_path, "agent"),
                f"unknown agent {agent!r}; defined: {', '.join(sorted(agents))}",
            )
            continue
        for key in data:
            if key not in ("agent", "model", "effort"):
                check.fail((*item_path, str(key)), "unknown field")
        rungs.append(
            Rung(
                agent=agent,
                model=check.string(data.get("model"), (*item_path, "model")),
                effort=check.string(data.get("effort"), (*item_path, "effort")),
            )
        )
    return tuple(rungs)


def build_profile(name: str, raw: Any, check: Validator) -> Profile:
    path = ("profiles", name)
    data = check.mapping(raw, path)
    for key in data:
        if key not in ("start_offset", "min_tier", "max_tier", "skip_tiers", "max_escalations"):
            check.fail((*path, str(key)), "unknown field")
    skip_raw = data.get("skip_tiers") or []
    skip: set[int] = set()
    if not isinstance(skip_raw, list):
        check.fail((*path, "skip_tiers"), "expected a list of tiers")
    else:
        for index, tier in enumerate(skip_raw):
            skip.add(check.integer(tier, (*path, "skip_tiers", str(index)), 0, 1, 4))
    max_escalations = data.get("max_escalations")
    min_tier = check.integer(data.get("min_tier"), (*path, "min_tier"), 1, 1, 4)
    max_tier = check.integer(data.get("max_tier"), (*path, "max_tier"), 4, 1, 4)
    if min_tier > max_tier:
        check.fail((*path, "min_tier"), f"min_tier {min_tier} is above max_tier {max_tier}")
    return Profile(
        name=name,
        start_offset=check.integer(data.get("start_offset"), (*path, "start_offset"), 0, -3, 3),
        min_tier=min_tier,
        max_tier=max_tier,
        skip_tiers=frozenset(skip - {0}),
        max_escalations=None
        if max_escalations is None
        else check.integer(max_escalations, (*path, "max_escalations"), 0, 0, 10),
    )


def build_execution(raw: Any, check: Validator) -> Execution:
    path = ("execution",)
    data = check.mapping(raw, path)
    for key in data:
        if key not in EXECUTION_FIELDS:
            check.fail((*path, str(key)), "unknown field")
    verify_env = check.strings(data.get("verify_env"), (*path, "verify_env"))
    for index, name in enumerate(verify_env):
        if not ENV_NAME_RE.match(name):
            check.fail((*path, "verify_env", str(index)), "invalid environment variable name")
    verify_raw = data.get("verify") or []
    verify: list[tuple[str, ...]] = []
    if not isinstance(verify_raw, list):
        check.fail((*path, "verify"), "expected a list of commands (each a list of arguments)")
    else:
        for index, command in enumerate(verify_raw):
            argv = check.strings(command, (*path, "verify", str(index)))
            if not argv:
                check.fail((*path, "verify", str(index)), "command must be a non-empty list")
            verify.append(argv)
    return Execution(
        max_escalations=check.integer(
            data.get("max_escalations"), (*path, "max_escalations"), 2, 0, 10
        ),
        review_min_tier=check.integer(
            data.get("review_min_tier"), (*path, "review_min_tier"), 3, 1, 5
        ),
        cooldown_minutes=check.integer(
            data.get("cooldown_minutes"), (*path, "cooldown_minutes"), 30, 0, 24 * 60
        ),
        total_timeout_s=check.integer(
            data.get("total_timeout_s"), (*path, "total_timeout_s"), 1800, 1, 86400
        ),
        idle_timeout_s=check.integer(
            data.get("idle_timeout_s"), (*path, "idle_timeout_s"), 300, 1, 86400
        ),
        verify=tuple(command for command in verify if command),
        verify_timeout_s=check.integer(
            data.get("verify_timeout_s"), (*path, "verify_timeout_s"), 900, 1, 86400
        ),
        verify_env=verify_env,
    )


def build_config(data: dict[str, Any], marks: Marks, source: Path | None) -> Config:
    check = Validator(marks)
    known = {
        "version",
        "agents",
        "rate_limit_patterns",
        "tiers",
        "classifier",
        "planner",
        "reviewer",
        "profiles",
        "execution",
        "adaptive",
        "decompose",
    }
    for key in data:
        if key not in known:
            check.fail((str(key),), "unknown field")
    if data.get("version", 1) != 1:
        check.fail(("version",), "only version 1 is supported")
    agents_raw = check.mapping(data.get("agents"), ("agents",))
    agents = {
        str(name): agent
        for name, raw in agents_raw.items()
        if (agent := build_agent(str(name), raw, check)) is not None
    }
    tiers_raw = check.mapping(data.get("tiers"), ("tiers",))
    tiers: dict[int, tuple[Rung, ...]] = {}
    for key, ladder in tiers_raw.items():
        try:
            tier = int(key)
        except (TypeError, ValueError):
            tier = 0
        if tier not in TIERS:
            check.fail(("tiers", str(key)), "tier must be 1, 2, 3 or 4")
            continue
        tiers[tier] = build_ladder(ladder, ("tiers", str(key)), agents_raw, check)
    if not any(tiers.values()):
        check.fail(("tiers",), "at least one tier needs a non-empty ladder")
    profiles_raw = check.mapping(data.get("profiles"), ("profiles",))
    profiles = {
        str(name): build_profile(str(name), raw, check) for name, raw in profiles_raw.items()
    }
    profiles.setdefault("balanced", Profile(name="balanced"))
    adaptive_raw = check.mapping(data.get("adaptive"), ("adaptive",))
    decompose_raw = check.mapping(data.get("decompose"), ("decompose",))
    config = Config(
        agents=agents,
        tiers=tiers,
        profiles=profiles,
        classifier=build_ladder(data.get("classifier"), ("classifier",), agents_raw, check),
        planner=build_ladder(data.get("planner"), ("planner",), agents_raw, check),
        reviewer=build_ladder(data.get("reviewer"), ("reviewer",), agents_raw, check),
        rate_limit_patterns=check.regexes(
            data.get("rate_limit_patterns"), ("rate_limit_patterns",)
        ),
        execution=build_execution(data.get("execution"), check),
        adaptive=Adaptive(
            enabled=check.boolean(adaptive_raw.get("enabled"), ("adaptive", "enabled"), True),
            min_samples=check.integer(
                adaptive_raw.get("min_samples"), ("adaptive", "min_samples"), 5, 1, 10000
            ),
        ),
        decompose=Decompose(
            auto=check.boolean(decompose_raw.get("auto"), ("decompose", "auto"), False),
            max_subtasks=check.integer(
                decompose_raw.get("max_subtasks"), ("decompose", "max_subtasks"), 6, 2, 20
            ),
            max_parallel=check.integer(
                decompose_raw.get("max_parallel"), ("decompose", "max_parallel"), 3, 1, 16
            ),
        ),
        source=source,
        raw=data,
    )
    if check.problems:
        raise ConfigError(check.problems)
    return config


def load_config(path: Path | None = None) -> Config:
    defaults, marks = parse_yaml(default_text(), "default_routes.yaml")
    target = path if path is not None else user_config_path()
    if path is not None and not target.is_file():
        raise ConfigError([f"{target}: file not found"])
    if not target.is_file():
        return build_config(defaults, marks, None)
    user_data, user_marks = parse_yaml(target.read_text(encoding="utf-8"), str(target))
    return build_config(deep_merge(defaults, user_data), {**marks, **user_marks}, target)


def validate_text(text: str, filename: str = "routes.yaml") -> Config:
    defaults, marks = parse_yaml(default_text(), "default_routes.yaml")
    user_data, user_marks = parse_yaml(text, filename)
    return build_config(deep_merge(defaults, user_data), {**marks, **user_marks}, None)
