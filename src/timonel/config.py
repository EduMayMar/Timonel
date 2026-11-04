from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import re
import types
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints

import yaml

from timonel import schema

Billing = Literal["subscription", "api"]
ParserKind = Literal["text", "json", "stream-json", "jsonl"]
PromptVia = Literal["arg", "stdin", "file"]
Layer = Literal["default", "user", "project", "env", "cli"]

PLACEHOLDERS = frozenset({"prompt", "prompt_file", "model", "effort", "timeout_s"})
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
DANGEROUS_MARKERS = (
    "--dangerously",
    "yolo",
    "danger-full-access",
    "bypasspermissions",
    "approval_policy=never",
    "--allow-tool=shell",
    "--skip-permissions",
    "--yes-always",
)
DANGEROUS_EXACT = frozenset({"--allow-all", "--allow-all-paths", "--allow-all-urls"})
TIERS = (1, 2, 3, 4)
LAYERS: tuple[Layer, ...] = ("default", "user", "project", "env", "cli")
ENV_PREFIX = "TIMONEL_"
PROJECT_FILE = ".timonel.yaml"
LEGACY_EXECUTION = {
    "verify": ("verification", "commands"),
    "verify_timeout_s": ("verification", "timeout_s"),
    "verify_env": ("verification", "env_allow"),
    "review_min_tier": ("review", "min_tier"),
    "max_escalations": ("escalation", "max_escalations"),
    "cooldown_minutes": ("escalation", "cooldown_minutes"),
}


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
class Provider:
    api: Literal["openai", "anthropic"]
    base_url: str
    key_env: str = ""
    key_required: bool = True
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 120
    max_tokens: int = 2048


@dataclass(frozen=True)
class Agent:
    name: str
    command: str
    billing: Billing = "api"
    parser: ParserKind = "text"
    kind: Literal["agent", "provider"] = "agent"
    enabled: bool = True
    verified: bool = False
    json_result_key: str = "result"
    json_cost_key: str = "total_cost_usd"
    json_error_key: str = "is_error"
    prompt_via: PromptVia = "arg"
    pty: bool = False
    supports_read_only: bool = True
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
    env_pass: tuple[str, ...] = ()
    models_command: tuple[str, ...] = ()
    known_models: tuple[str, ...] = ()
    model_aliases: dict[str, str] = field(default_factory=dict)
    rate_limit_patterns: tuple[str, ...] = ()
    cost_per_attempt_usd: float | None = None
    price_input_per_mtok_usd: float | None = None
    price_output_per_mtok_usd: float | None = None
    total_timeout_s: int | None = None
    idle_timeout_s: int | None = None
    max_concurrent: int = 4
    detect: Detect = field(default_factory=Detect)
    provider: Provider | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def model_name(self, model: str) -> str:
        return self.model_aliases.get(model, model)


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
    name: str = ""
    start_offset: int = 0
    min_tier: int = 1
    max_tier: int = 4
    skip_tiers: frozenset[int] = frozenset()
    max_escalations: int | None = None


@dataclass(frozen=True)
class RuleWhen:
    kinds: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    min_chars: int | None = None
    max_chars: int | None = None
    min_complexity: int | None = None
    max_complexity: int | None = None


@dataclass(frozen=True)
class RuleThen:
    profile: str | None = None
    force: str | None = None
    min_tier: int | None = None
    max_tier: int | None = None
    deny_agents: tuple[str, ...] = ()
    only_agents: tuple[str, ...] = ()
    verify: tuple[tuple[str, ...], ...] | None = None


@dataclass(frozen=True)
class Rule:
    then: RuleThen = field(default_factory=RuleThen)
    when: RuleWhen = field(default_factory=RuleWhen)
    name: str = ""


@dataclass(frozen=True)
class Routing:
    prefer_subscription: bool = True
    deny_agents: tuple[str, ...] = ()
    deny_models: tuple[str, ...] = ()
    rules: tuple[Rule, ...] = ()


@dataclass(frozen=True)
class BaseComplexity:
    explain: int = 1
    review: int = 2
    edit: int = 2
    debug: int = 2
    refactor: int = 3
    architecture: int = 4


@dataclass(frozen=True)
class Keywords:
    review: tuple[str, ...] = ()
    architecture: tuple[str, ...] = ()
    debug: tuple[str, ...] = ()
    refactor: tuple[str, ...] = ()
    explain: tuple[str, ...] = ()


@dataclass(frozen=True)
class Heuristics:
    base_complexity: BaseComplexity = field(default_factory=BaseComplexity)
    long_task_chars: int = 400
    long_task_bonus: int = 1
    many_files: int = 3
    many_files_bonus: int = 1
    broad_bonus: int = 1
    trivial_penalty: int = 1
    trivial_max_chars: int = 200
    high_risk_raises_tier: bool = True
    keywords: Keywords = field(default_factory=Keywords)
    broad_words: tuple[str, ...] = ()
    trivial_words: tuple[str, ...] = ()
    risky_words: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassifierSettings:
    use_model: bool = True
    ladder: tuple[Rung, ...] = ()
    retries: int = 0
    on_failure: Literal["heuristic", "fail"] = "heuristic"
    prompt_template: str = ""
    heuristics: Heuristics = field(default_factory=Heuristics)


@dataclass(frozen=True)
class PlannerSettings:
    ladder: tuple[Rung, ...] = ()
    prompt_template: str = ""


@dataclass(frozen=True)
class ReviewSettings:
    enabled: bool = True
    min_tier: int = 3
    reviewers: tuple[Rung, ...] = ()
    max_reviewers_tried: int = 3
    strictness: Literal["lenient", "normal", "strict"] = "normal"
    max_rounds: int = 1
    on_no_verdict: Literal["approve", "reject"] = "approve"
    diff_chars: int = 60000
    prompt_template: str = ""


@dataclass(frozen=True)
class Escalation:
    max_escalations: int = 2
    on_verify_failure: Literal["next_tier", "next_rung"] = "next_tier"
    on_agent_failure: Literal["next_rung", "next_tier"] = "next_rung"
    context_chars: int = 4000
    context_attempts: int = 2
    cooldown_minutes: int = 30
    reset_between_attempts: bool = True


@dataclass(frozen=True)
class Detector:
    file: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class Verification:
    commands: tuple[tuple[str, ...], ...] = ()
    autodetect: bool = False
    detectors: tuple[Detector, ...] = ()
    timeout_s: int = 900
    success_exit_codes: tuple[int, ...] = (0,)
    env_allow: tuple[str, ...] = ()


@dataclass(frozen=True)
class Execution:
    total_timeout_s: int = 1800
    idle_timeout_s: int = 300
    orphan_grace_s: float = 2.0
    worktree_dir: str = ""
    branch_pattern: str = "timonel/{id}"
    base_ref: str = "HEAD"
    cleanup_on_apply: bool = True
    write_kinds: tuple[str, ...] = ("edit", "debug", "refactor", "architecture")
    agent_env_deny: tuple[str, ...] = ()
    max_output_chars: int = 4000000
    stored_output_chars: int = 200000
    max_parallel_runs: int = 2
    race_contenders: int = 2


@dataclass(frozen=True)
class Adaptive:
    enabled: bool = True
    min_samples: int = 5


@dataclass(frozen=True)
class Decompose:
    auto: bool = False
    min_complexity: int = 4
    max_subtasks: int = 6
    max_parallel: int = 3


@dataclass(frozen=True)
class Budget:
    period: Literal["day", "week", "month"]
    name: str = ""
    agent: str = "*"
    max_usd: float | None = None
    max_tokens: int | None = None
    max_requests: int | None = None
    action: Literal["skip", "ask", "stop"] = "skip"


@dataclass(frozen=True)
class Privacy:
    log_level: str = "warning"
    log_file: str = ""
    redact_patterns: tuple[str, ...] = ()
    secret_env_names: str = "(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
    store_outputs: bool = True
    retention_days: int = 0
    update_check: bool = False


@dataclass(frozen=True)
class Shortcuts:
    panel: str = "p"
    new: str = "n"
    history: str = "h"
    stats: str = "s"
    routes: str = "r"
    setup: str = "i"
    settings: str = "c"
    theme: str = "t"
    language: str = "l"


@dataclass(frozen=True)
class UiSettings:
    host: str = "127.0.0.1"
    port: int = 0
    open_browser: bool = True
    theme: str = "auto"
    density: str = "comfortable"
    tour: bool = True
    shortcuts: Shortcuts = field(default_factory=Shortcuts)


@dataclass(frozen=True)
class Notifications:
    events: tuple[str, ...] = ("succeeded", "failed")
    desktop: bool = False
    sound: bool = False
    webhook_url: str = ""


@dataclass(frozen=True)
class Hooks:
    pre_run: tuple[tuple[str, ...], ...] = ()
    post_run: tuple[tuple[str, ...], ...] = ()
    on_failure: tuple[tuple[str, ...], ...] = ()
    timeout_s: int = 120


@dataclass(frozen=True)
class Paths:
    data_dir: str = ""
    ledger: str = ""
    cache_dir: str = ""


@dataclass(frozen=True)
class ProjectState:
    path: Path | None = None
    trusted: bool = False
    digest: str = ""
    text: str = field(default="", repr=False)


@dataclass(frozen=True)
class Config:
    agents: dict[str, Agent]
    tiers: dict[int, tuple[Rung, ...]]
    profiles: dict[str, Profile]
    version: int = 1
    mode: str = "simple"
    language: str = "auto"
    default_profile: str = "balanced"
    rate_limit_patterns: tuple[str, ...] = ()
    routing: Routing = field(default_factory=Routing)
    classifier: ClassifierSettings = field(default_factory=ClassifierSettings)
    planner: PlannerSettings = field(default_factory=PlannerSettings)
    review: ReviewSettings = field(default_factory=ReviewSettings)
    escalation: Escalation = field(default_factory=Escalation)
    verification: Verification = field(default_factory=Verification)
    execution: Execution = field(default_factory=Execution)
    adaptive: Adaptive = field(default_factory=Adaptive)
    decompose: Decompose = field(default_factory=Decompose)
    budgets: tuple[Budget, ...] = ()
    privacy: Privacy = field(default_factory=Privacy)
    ui: UiSettings = field(default_factory=UiSettings)
    notifications: Notifications = field(default_factory=Notifications)
    hooks: Hooks = field(default_factory=Hooks)
    paths: Paths = field(default_factory=Paths)
    source: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    origins: dict[str, Layer] = field(default_factory=dict)
    layers: dict[str, dict[str, Any]] = field(default_factory=dict)
    project: ProjectState = field(default_factory=ProjectState)

    def usable_agents(self) -> dict[str, Agent]:
        return {name: agent for name, agent in self.agents.items() if agent.enabled}


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


def user_agents_dir() -> Path:
    return user_config_dir() / "agents"


def trust_store_path() -> Path:
    return user_config_dir() / "trusted-projects.json"


def collect_marks(node: yaml.Node, filename: str, path: tuple[str, ...], marks: Marks) -> None:
    marks.setdefault(path, (filename, node.start_mark.line + 1))
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            child = (*path, str(key_node.value))
            marks[child] = (filename, key_node.start_mark.line + 1)
            collect_marks(value_node, filename, child, marks)
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
    return stringify_keys(data), marks


def stringify_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): stringify_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stringify_keys(item) for item in value]
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and key != "tiers":
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def flatten(data: Any, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    if isinstance(data, dict) and data and path != ("tiers",):
        result: dict[tuple[str, ...], Any] = {}
        for key, value in data.items():
            result.update(flatten(value, (*path, str(key))))
        return result
    return {path: data} if path else {}


def migrate(data: dict[str, Any], marks: Marks | None = None) -> dict[str, Any]:
    data = copy.deepcopy(data)
    moves: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    execution = data.get("execution")
    if isinstance(execution, dict):
        for old, (section, key) in LEGACY_EXECUTION.items():
            if old in execution:
                data.setdefault(section, {})
                if isinstance(data[section], dict):
                    data[section].setdefault(key, execution.pop(old))
                    moves.append((("execution", old), (section, key)))
    for old, (section, key) in {
        "classifier": ("classifier", "ladder"),
        "planner": ("planner", "ladder"),
        "reviewer": ("review", "reviewers"),
    }.items():
        if isinstance(data.get(old), list):
            ladder = data.pop(old)
            data.setdefault(section, {})
            if isinstance(data[section], dict):
                data[section][key] = ladder
                moves.append(((old,), (section, key)))
    if marks is not None:
        for source, target in moves:
            for path, mark in list(marks.items()):
                if path[: len(source)] == source:
                    marks[(*target, *path[len(source) :])] = mark
    return data


def registry_files() -> list[tuple[str, str]]:
    package = resources.files("timonel").joinpath("agents")
    found = [
        (f"agents/{entry.name}", entry.read_text(encoding="utf-8"))
        for entry in sorted(package.iterdir(), key=lambda item: item.name)
        if entry.name.endswith(".yaml")
    ]
    user_dir = user_agents_dir()
    if user_dir.is_dir():
        found.extend(
            (str(path), path.read_text(encoding="utf-8"))
            for path in sorted(user_dir.glob("*.yaml"))
        )
    return found


CONNECTOR_KEYS = frozenset(
    {
        "name",
        "kind",
        "title",
        "vendor",
        "homepage",
        "verification",
        "install",
        "login",
        "api_key",
        "agent",
        "provider",
        "description",
    }
)


def connector_to_agent(data: dict[str, Any], filename: str) -> tuple[str, dict[str, Any]]:
    problems = [
        f"{filename}: unknown connector field {key!r}" for key in data if key not in CONNECTOR_KEYS
    ]
    name = data.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        problems.append(f"{filename}: name must be lowercase letters, digits, - or _")
    agent = data.get("agent")
    if not isinstance(agent, dict):
        problems.append(f"{filename}: agent section is required")
    if problems:
        raise ConfigError(problems)
    entry = dict(cast(dict[str, Any], agent))
    entry["kind"] = data.get("kind", "agent")
    if "provider" in data:
        entry["provider"] = data["provider"]
    status = (data.get("verification") or {}).get("status")
    entry.setdefault("verified", status == "verified")
    entry["meta"] = {
        key: data[key]
        for key in (
            "title",
            "vendor",
            "homepage",
            "verification",
            "install",
            "login",
            "api_key",
            "description",
        )
        if key in data
    }
    return cast(str, name), entry


def load_registry() -> dict[str, Any]:
    agents: dict[str, Any] = {}
    for filename, text in registry_files():
        data, _ = parse_yaml(text, filename)
        name, entry = connector_to_agent(data, filename)
        agents[name] = deep_merge(agents.get(name, {}), entry)
    return agents


def default_layer() -> tuple[dict[str, Any], Marks]:
    routes, marks = parse_yaml(default_text(), "default_routes.yaml")
    base = deep_merge(schema.defaults(), {"agents": load_registry()})
    return deep_merge(base, migrate(routes)), marks


def env_layer(environ: dict[str, str]) -> dict[str, Any]:
    top = set(schema.load_schema()["properties"])
    layer: dict[str, Any] = {}
    for name, raw in sorted(environ.items()):
        if not name.startswith(ENV_PREFIX):
            continue
        path = [part.lower() for part in name[len(ENV_PREFIX) :].split("__") if part]
        if not path or path[0] not in top:
            continue
        set_path(layer, path, parse_scalar(raw))
    return layer


def parse_scalar(raw: str) -> Any:
    try:
        return yaml.safe_load(raw) if raw.strip() else ""
    except yaml.YAMLError:
        return raw


def set_path(data: dict[str, Any], path: list[str], value: Any) -> None:
    target = data
    for part in path[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    target[path[-1]] = value


def get_path(data: Any, path: list[str]) -> Any:
    for part in path:
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            raise KeyError(".".join(path))
    return data


def unset_path(data: dict[str, Any], path: list[str]) -> bool:
    target: Any = data
    for part in path[:-1]:
        if not isinstance(target, dict) or part not in target:
            return False
        target = target[part]
    if isinstance(target, dict) and path[-1] in target:
        del target[path[-1]]
        return True
    return False


def overrides_layer(assignments: list[str]) -> dict[str, Any]:
    layer: dict[str, Any] = {}
    for assignment in assignments:
        key, separator, raw = assignment.partition("=")
        if not separator or not key.strip():
            raise ConfigError([f"--set {assignment!r}: use key=value"])
        set_path(layer, key.strip().split("."), parse_scalar(raw))
    return layer


def read_trust_store() -> dict[str, str]:
    path = trust_store_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def write_trust_store(store: dict[str, str]) -> None:
    path = trust_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")


def project_file(repo: Path | None) -> Path | None:
    if repo is None:
        return None
    candidate = repo / PROJECT_FILE
    return candidate if candidate.is_file() else None


def project_state(repo: Path | None) -> ProjectState:
    path = project_file(repo)
    if path is None:
        return ProjectState()
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    trusted = read_trust_store().get(str(path.resolve())) == digest
    return ProjectState(path=path, trusted=trusted, digest=digest, text=content.decode("utf-8"))


def trust_project(path: Path, content: bytes | None = None) -> str:
    store = read_trust_store()
    digest = hashlib.sha256(path.read_bytes() if content is None else content).hexdigest()
    store[str(path.resolve())] = digest
    write_trust_store(store)
    return digest


def revoke_project(path: Path) -> bool:
    store = read_trust_store()
    removed = store.pop(str(path.resolve()), None) is not None
    write_trust_store(store)
    return removed


def is_dangerous(token: str) -> bool:
    lowered = token.lower()
    return token in DANGEROUS_EXACT or any(marker in lowered for marker in DANGEROUS_MARKERS)


def where(marks: Marks, path: tuple[str, ...]) -> str:
    probe = path
    while probe and probe not in marks:
        probe = probe[:-1]
    filename, line = marks.get(probe, ("routes.yaml", 0))
    return f"{filename}:{line}"


def convert(kind: Any, value: Any) -> Any:
    origin = get_origin(kind)
    args = get_args(kind)
    if origin in (Union, types.UnionType):
        if value is None:
            return None
        inner = [arg for arg in args if arg is not type(None)]
        return convert(inner[0], value)
    if dataclasses.is_dataclass(kind) and isinstance(value, dict):
        return build(cast(type, kind), value)
    if origin is tuple and isinstance(value, list | tuple):
        item = args[0]
        return tuple(convert(item, element) for element in value)
    if origin is frozenset:
        return frozenset(value or ())
    if origin is dict and isinstance(value, dict):
        return {key: convert(args[1], item) for key, item in value.items()}
    if kind is float and isinstance(value, int):
        return float(value)
    return value


def build(cls: type, data: dict[str, Any], **extra: Any) -> Any:
    hints = get_type_hints(cls)
    names = {item.name for item in dataclasses.fields(cls)}
    values = {key: convert(hints[key], value) for key, value in data.items() if key in names}
    return cls(**{**values, **extra})


class Checker:
    def __init__(self, marks: Marks) -> None:
        self.marks = marks
        self.problems: list[str] = []

    def fail(self, path: tuple[str, ...], message: str) -> None:
        self.problems.append(f"{where(self.marks, path)}: {'.'.join(path) or '<root>'}: {message}")

    def template(self, tokens: list[str], path: tuple[str, ...]) -> None:
        for index, token in enumerate(tokens):
            for name in PLACEHOLDER_RE.findall(token):
                if name not in PLACEHOLDERS:
                    allowed = ", ".join(sorted(PLACEHOLDERS))
                    self.fail((*path, str(index)), f"unknown placeholder {{{name}}}; use {allowed}")
            if is_dangerous(token):
                self.fail(
                    (*path, str(index)), f"{token!r} bypasses safety; move it to dangerous_args"
                )

    def agent(self, name: str, data: dict[str, Any]) -> None:
        path = ("agents", name)
        if not data.get("command"):
            self.fail((*path, "command"), "is required")
        for group in (
            "base_args",
            "read_args",
            "write_args",
            "model_args",
            "effort_args",
            "prompt_args",
        ):
            self.template(list(data.get(group) or []), (*path, group))
        prompt_args = list(data.get("prompt_args") or [])
        via = data.get("prompt_via", "arg")
        uses = {name for token in prompt_args for name in PLACEHOLDER_RE.findall(token)}
        if data.get("kind", "agent") == "agent":
            if via == "arg" and "prompt" not in uses:
                self.fail(
                    (*path, "prompt_args"), "prompt_via is 'arg' but no token contains {prompt}"
                )
            if via == "file" and "prompt_file" not in uses:
                self.fail(
                    (*path, "prompt_args"),
                    "prompt_via is 'file' but no token contains {prompt_file}",
                )
            if via == "stdin" and uses & {"prompt", "prompt_file"}:
                self.fail(
                    (*path, "prompt_args"), "prompt_via is 'stdin' so {prompt} must not appear"
                )
            if data.get("pty") and via == "stdin":
                self.fail((*path, "pty"), "a pseudo-terminal agent needs prompt_via: arg")
        elif not isinstance(data.get("provider"), dict):
            self.fail((*path, "provider"), "a provider connector needs a provider section")

    def ladder(
        self, ladder: Any, path: tuple[str, ...], agents: dict[str, Any], providers_ok: bool
    ) -> None:
        for index, rung in enumerate(ladder or []):
            name = rung.get("agent") if isinstance(rung, dict) else None
            item = (*path, str(index), "agent")
            if name not in agents:
                hint = schema.suggestion(str(name), sorted(agents))
                self.fail(item, f"unknown agent {name!r}{hint}")
            elif not providers_ok and agents[name].get("kind") == "provider":
                self.fail(item, f"{name!r} is an API provider; tiers need agents that edit code")


def check_config(data: dict[str, Any], marks: Marks) -> list[str]:
    checker = Checker(marks)
    for problem in schema.validate(data):
        checker.fail(problem.path, problem.message)
    if checker.problems:
        return checker.problems
    agents = data.get("agents", {})
    for name, agent in agents.items():
        checker.agent(name, agent)
    tiers = data.get("tiers") or {}
    for tier, ladder in tiers.items():
        checker.ladder(ladder, ("tiers", tier), agents, providers_ok=False)
    if not any(tiers.values()):
        checker.fail(("tiers",), "at least one tier needs a non-empty ladder")
    checker.ladder(data.get("classifier", {}).get("ladder"), ("classifier", "ladder"), agents, True)
    checker.ladder(data.get("planner", {}).get("ladder"), ("planner", "ladder"), agents, True)
    checker.ladder(data.get("review", {}).get("reviewers"), ("review", "reviewers"), agents, True)
    for name, profile in (data.get("profiles") or {}).items():
        if profile.get("min_tier", 1) > profile.get("max_tier", 4):
            checker.fail(
                ("profiles", name, "min_tier"),
                f"min_tier {profile['min_tier']} is above max_tier {profile['max_tier']}",
            )
    if (
        data.get("default_profile") not in (data.get("profiles") or {})
        and data.get("default_profile") != "balanced"
    ):
        hint = schema.suggestion(
            str(data.get("default_profile")), sorted(data.get("profiles") or {})
        )
        checker.fail(("default_profile",), f"unknown profile {data.get('default_profile')!r}{hint}")
    for index, rule in enumerate(data.get("routing", {}).get("rules", [])):
        profile = rule.get("then", {}).get("profile")
        if profile is not None and profile not in (data.get("profiles") or {}):
            checker.fail(
                ("routing", "rules", str(index), "then", "profile"), f"unknown profile {profile!r}"
            )
    return checker.problems


def materialize(
    data: dict[str, Any],
    marks: Marks,
    source: Path | None,
    origins: dict[str, Layer],
    layers: dict[str, dict[str, Any]],
    project: ProjectState,
) -> Config:
    problems = check_config(data, marks)
    if problems:
        raise ConfigError(problems)
    agents = {name: build(Agent, entry, name=name) for name, entry in data["agents"].items()}
    tiers = {
        int(tier): tuple(build(Rung, rung) for rung in ladder)
        for tier, ladder in data["tiers"].items()
    }
    profiles = {
        name: build(Profile, value, name=name)
        for name, value in (data.get("profiles") or {}).items()
    }
    profiles.setdefault("balanced", Profile(name="balanced"))
    rest = {key: value for key, value in data.items() if key not in ("agents", "tiers", "profiles")}
    return cast(
        Config,
        build(
            Config,
            rest,
            agents=agents,
            tiers=tiers,
            profiles=profiles,
            source=source,
            raw=data,
            origins=origins,
            layers=layers,
            project=project,
        ),
    )


def compose(layers: list[tuple[Layer, dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, Layer]]:
    merged: dict[str, Any] = {}
    origins: dict[str, Layer] = {}
    for layer, data in layers:
        merged = deep_merge(merged, data)
        for path in flatten(data):
            origins[".".join(path)] = layer
    return merged, origins


def load_config(
    path: Path | None = None,
    *,
    repo: Path | None = None,
    overrides: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    defaults, marks = default_layer()
    target = path if path is not None else user_config_path()
    if path is not None and not target.is_file():
        raise ConfigError([f"{target}: file not found"])
    user: dict[str, Any] = {}
    if target.is_file():
        user_raw, user_marks = parse_yaml(target.read_text(encoding="utf-8"), str(target))
        user = migrate(user_raw, user_marks)
        marks = {**marks, **user_marks}
    project = project_state(repo)
    project_data: dict[str, Any] = {}
    if project.path is not None and project.trusted:
        project_raw, project_marks = parse_yaml(project.text, str(project.path))
        forbidden = project_problems(project_raw, str(project.path))
        if forbidden:
            raise ConfigError(forbidden)
        project_data = migrate(project_raw, project_marks)
        marks = {**marks, **project_marks}
    env = env_layer(dict(os.environ if environ is None else environ))
    cli = overrides_layer(overrides or [])
    ordered: list[tuple[Layer, dict[str, Any]]] = [
        ("default", defaults),
        ("user", user),
        ("project", project_data),
        ("env", env),
        ("cli", cli),
    ]
    merged, origins = compose(ordered)
    return materialize(
        merged, marks, target if target.is_file() else None, origins, dict(ordered), project
    )


def validate_text(text: str, filename: str = "routes.yaml") -> Config:
    defaults, marks = default_layer()
    user_raw, user_marks = parse_yaml(text, filename)
    user = migrate(user_raw, user_marks)
    ordered: list[tuple[Layer, dict[str, Any]]] = [("default", defaults), ("user", user)]
    merged, origins = compose(ordered)
    return materialize(
        merged, {**marks, **user_marks}, None, origins, dict(ordered), ProjectState()
    )


SENSITIVE_PROJECT_AREAS = ("agents", "verification", "notifications", "paths", "privacy")
PROJECT_FORBIDDEN_AGENT_KEYS = ("meta", "provider", "env_from", "env_pass")


def project_commands(data: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for command in data.get("verification", {}).get("commands", []) or []:
        found.append("verify: " + " ".join(map(str, command)))
    for hook in ("pre_run", "post_run", "on_failure"):
        for command in data.get("hooks", {}).get(hook, []) or []:
            found.append(f"hook {hook}: " + " ".join(map(str, command)))
    for index, rule in enumerate(data.get("routing", {}).get("rules", []) or []):
        for command in (rule.get("then", {}) or {}).get("verify", []) or []:
            found.append(f"rule {index} verify: " + " ".join(map(str, command)))
    for area in SENSITIVE_PROJECT_AREAS:
        for path in flatten(data.get(area) or {}):
            key = ".".join((area, *path))
            if not key.startswith("verification.commands"):
                found.append(f"sets {key}")
    if (data.get("execution") or {}).get("agent_env_deny") is not None:
        found.append("sets execution.agent_env_deny")
    return found


def project_problems(data: dict[str, Any], filename: str) -> list[str]:
    return [
        f"{filename}: agents.{name}.{key} can only be set in your user configuration"
        for name, agent in (data.get("agents") or {}).items()
        if isinstance(agent, dict)
        for key in PROJECT_FORBIDDEN_AGENT_KEYS
        if key in agent
    ]
