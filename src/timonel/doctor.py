from __future__ import annotations

import importlib.util
import os
import re
import tempfile
from collections.abc import Collection, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from timonel.adapters import (
    SecretMissingError,
    build_env,
    build_invocation,
    parse_output,
    passed_through,
    secret_sources,
    strip_ansi,
)
from timonel.config import Agent, Config, Rung
from timonel.proc import LaunchError, ProcResult, run_process, which

AuthState = Literal["ok", "missing", "unknown"]
SMOKE_PROMPT = "Reply with exactly the word: pong"
MODEL_LINE = re.compile(r"^[\s*>-]*([A-Za-z0-9][\w.:/\[\]-]*)")


@dataclass(frozen=True)
class AgentHealth:
    name: str
    command: str
    installed: bool
    path: str | None
    version: str
    auth: AuthState
    auth_detail: str
    billing: str
    verified: bool
    models: tuple[str, ...] = ()
    smoke: str = ""
    smoke_ok: bool | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.installed and self.auth != "missing"


def quick(argv: list[str], env: Mapping[str, str], timeout_s: float = 20) -> ProcResult | None:
    try:
        return run_process(
            argv,
            cwd=Path(tempfile.gettempdir()),
            env=env,
            total_timeout_s=timeout_s,
            idle_timeout_s=timeout_s,
        )
    except (LaunchError, OSError):
        return None


def first_line(text: str) -> str:
    for line in strip_ansi(text).splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""


def safe_env(agent: Agent, config: Config, environ: Mapping[str, str]) -> dict[str, str]:
    try:
        return build_env(agent, config.agents, environ)
    except SecretMissingError:
        blocked = secret_sources(config.agents) - passed_through(agent)
        return {key: value for key, value in environ.items() if key not in blocked}


def check_auth(
    agent: Agent, env: Mapping[str, str], environ: Mapping[str, str]
) -> tuple[AuthState, str]:
    if agent.detect.auth_env:
        present = [name for name in agent.detect.auth_env if environ.get(name)]
        if present:
            return "ok", f"{present[0]} is set"
        if not agent.detect.auth:
            if agent.env_from:
                return "missing", f"set {' or '.join(agent.detect.auth_env)}"
            return "unknown", f"no {' / '.join(agent.detect.auth_env)}; may use a stored login"
    if not agent.detect.auth:
        return "unknown", "no auth check declared"
    result = quick([agent.command, *agent.detect.auth], env)
    if result is None or result.timed_out:
        return "unknown", "auth check did not finish"
    output = strip_ansi(result.output)
    if agent.detect.auth_ok_pattern:
        if re.search(agent.detect.auth_ok_pattern, output):
            return "ok", "logged in"
        return "missing", first_line(output) or "not logged in"
    if result.exit_code == 0:
        return "ok", first_line(output) or "logged in"
    return "missing", first_line(output) or f"exit code {result.exit_code}"


def parse_models(output: str) -> tuple[str, ...]:
    models: list[str] = []
    for line in strip_ansi(output).splitlines():
        match = MODEL_LINE.match(line)
        if match and not line.strip().endswith(":") and match.group(1) not in models:
            models.append(match.group(1))
    return tuple(models[:100])


def list_models(agent: Agent, env: Mapping[str, str]) -> tuple[str, ...]:
    if not agent.models_command:
        return agent.known_models
    result = quick([agent.command, *agent.models_command], env, timeout_s=30)
    if result is None or result.exit_code != 0:
        return agent.known_models
    return parse_models(result.output) or agent.known_models


def smoke_test(agent: Agent, config: Config, environ: Mapping[str, str]) -> tuple[bool, str]:
    rung = Rung(agent=agent.name)
    try:
        invocation = build_invocation(
            agent, rung, SMOKE_PROMPT, config.agents, write=False, timeout_s=120, environ=environ
        )
        with tempfile.TemporaryDirectory(prefix="timonel-smoke-") as workdir:
            result = run_process(
                invocation.argv,
                cwd=Path(workdir),
                env=invocation.env,
                stdin=invocation.stdin,
                use_pty=invocation.pty,
                total_timeout_s=180,
                idle_timeout_s=120,
            )
    except (SecretMissingError, LaunchError, OSError) as error:
        return False, str(error)
    if result.timed_out or result.idle_timed_out:
        return False, "timed out"
    parsed = parse_output(agent, result.output)
    ok = result.exit_code == 0 and not parsed.is_error and "pong" in parsed.text.lower()
    detail = f"exit {result.exit_code}, parsed={parsed.parsed}, reply={first_line(parsed.text)!r}"
    return ok, detail


def inspect_agent(
    agent: Agent,
    config: Config,
    *,
    smoke: bool = False,
    models: bool = True,
    environ: Mapping[str, str] | None = None,
) -> AgentHealth:
    source = dict(os.environ if environ is None else environ)
    path = which(agent.command, source.get("PATH"))
    if path is None:
        return AgentHealth(
            name=agent.name,
            command=agent.command,
            installed=False,
            path=None,
            version="",
            auth="unknown",
            auth_detail="not installed",
            billing=agent.billing,
            verified=agent.verified,
            models=agent.known_models,
        )
    env = safe_env(agent, config, source)
    version_result = quick([agent.command, *agent.detect.version], env)
    version = first_line(version_result.output) if version_result else ""
    auth, detail = check_auth(agent, env, source)
    found_models = list_models(agent, env) if models and auth != "missing" else agent.known_models
    smoke_ok: bool | None = None
    smoke_detail = ""
    if smoke and auth != "missing":
        smoke_ok, smoke_detail = smoke_test(agent, config, source)
    problems = []
    if agent.pty and os.name == "nt" and importlib.util.find_spec("winpty") is None:
        problems.append("pywinpty is required for this agent on Windows")
    return AgentHealth(
        name=agent.name,
        command=agent.command,
        installed=True,
        path=path,
        version=version,
        auth=auth,
        auth_detail=detail,
        billing=agent.billing,
        verified=agent.verified,
        models=found_models,
        smoke=smoke_detail,
        smoke_ok=smoke_ok,
        problems=tuple(problems),
    )


def default_names(config: Config) -> set[str]:
    ladders = [
        *config.classifier.ladder,
        *config.planner.ladder,
        *config.review.reviewers,
        *(rung for rungs in config.tiers.values() for rung in rungs),
    ]
    used = {rung.agent for rung in ladders}
    return {
        name
        for name, agent in config.agents.items()
        if agent.enabled and (agent.kind == "agent" or name in used)
    }


def diagnose(
    config: Config,
    *,
    smoke: bool = False,
    models: bool = True,
    environ: Mapping[str, str] | None = None,
    names: Collection[str] | None = None,
) -> list[AgentHealth]:
    wanted = set(names) if names is not None else default_names(config)
    agents = [agent for name, agent in config.agents.items() if name in wanted]
    with ThreadPoolExecutor(max_workers=max(1, len(agents))) as pool:
        return list(
            pool.map(
                lambda agent: inspect_agent(
                    agent, config, smoke=smoke, models=models, environ=environ
                ),
                agents,
            )
        )
