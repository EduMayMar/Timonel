from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from timonel.config import PLACEHOLDER_RE, Agent, Rung

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


class SecretMissingError(Exception):
    pass


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    env: dict[str, str]
    stdin: str | None
    pty: bool
    prompt_file: str | None = None


@dataclass(frozen=True)
class AgentOutput:
    text: str
    cost_usd: float | None
    is_error: bool
    parsed: bool
    tokens_in: int | None = None
    tokens_out: int | None = None


def fill(token: str, values: Mapping[str, str]) -> str:
    return PLACEHOLDER_RE.sub(lambda match: values.get(match.group(1), match.group(0)), token)


def render_argv(
    agent: Agent,
    rung: Rung,
    prompt: str,
    *,
    write: bool,
    timeout_s: int,
    dangerous: bool = False,
    prompt_file: str = "",
) -> list[str]:
    values = {
        "prompt": prompt,
        "prompt_file": prompt_file,
        "model": agent.model_name(rung.model),
        "effort": rung.effort,
        "timeout_s": str(timeout_s),
    }
    groups: list[tuple[str, ...]] = [
        agent.base_args,
        agent.write_args if write else agent.read_args,
    ]
    if rung.model:
        groups.append(agent.model_args)
    if rung.effort:
        groups.append(agent.effort_args)
    if dangerous and agent.allow_dangerous:
        groups.append(agent.dangerous_args)
    groups.append(agent.prompt_args)
    return [agent.command, *(fill(token, values) for group in groups for token in group)]


def lookup_secret(name: str, environ: Mapping[str, str]) -> str | None:
    if value := environ.get(name):
        return value
    try:
        import keyring
    except ImportError:
        return None
    try:
        stored = keyring.get_password("timonel", name)
    except Exception:
        return None
    return str(stored) if stored else None


def secret_sources(agents: Mapping[str, Agent]) -> set[str]:
    return {source for agent in agents.values() for source in agent.env_from.values()}


def denied(name: str, patterns: tuple[str, ...]) -> bool:
    return any(re.fullmatch(pattern, name, re.IGNORECASE) for pattern in patterns)


def build_env(
    agent: Agent,
    agents: Mapping[str, Agent],
    environ: Mapping[str, str] | None = None,
    deny: tuple[str, ...] = (),
) -> dict[str, str]:
    source = dict(os.environ if environ is None else environ)
    blocked = secret_sources(agents)
    env = {
        key: value for key, value in source.items() if key not in blocked and not denied(key, deny)
    }
    for name in agent.env_remove:
        env.pop(name, None)
    env.update(agent.env)
    for target, secret_name in agent.env_from.items():
        secret = lookup_secret(secret_name, source)
        if secret is None:
            raise SecretMissingError(
                f"{agent.name} needs {secret_name} (environment variable or keyring 'timonel')"
            )
        env[target] = secret
    env.setdefault("NO_COLOR", "1")
    env.setdefault("PYTHONUTF8", "1")
    return env


def build_invocation(
    agent: Agent,
    rung: Rung,
    prompt: str,
    agents: Mapping[str, Agent],
    *,
    write: bool,
    timeout_s: int,
    dangerous: bool = False,
    environ: Mapping[str, str] | None = None,
    prompt_file: str = "",
    deny: tuple[str, ...] = (),
) -> Invocation:
    return Invocation(
        argv=render_argv(
            agent,
            rung,
            prompt,
            write=write,
            timeout_s=timeout_s,
            dangerous=dangerous,
            prompt_file=prompt_file,
        ),
        env=build_env(agent, agents, environ, deny),
        stdin=prompt if agent.prompt_via == "stdin" else None,
        pty=agent.pty,
        prompt_file=prompt_file or None,
    )


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def usage_tokens(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return as_int(usage.get("input_tokens")), as_int(usage.get("output_tokens"))


def parse_json_result(
    payload: Any,
    raw: str,
    result_key: str = "result",
    cost_key: str = "total_cost_usd",
    error_key: str = "is_error",
) -> AgentOutput:
    if not isinstance(payload, dict):
        return AgentOutput(text=raw, cost_usd=None, is_error=True, parsed=False)
    result = payload.get(result_key)
    tokens_in, tokens_out = usage_tokens(payload)
    return AgentOutput(
        text=result if isinstance(result, str) else raw,
        cost_usd=as_float(payload.get(cost_key)),
        is_error=bool(payload.get(error_key)) or payload.get("subtype") not in (None, "success"),
        parsed=isinstance(result, str),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def parse_json(raw: str, agent: Agent | None = None) -> AgentOutput:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return AgentOutput(text=raw, cost_usd=None, is_error=False, parsed=False)
    if agent is None:
        return parse_json_result(payload, raw)
    return parse_json_result(
        payload, raw, agent.json_result_key, agent.json_cost_key, agent.json_error_key
    )


def json_lines(raw: str) -> list[dict[str, Any]]:
    events = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def parse_stream_json(raw: str) -> AgentOutput:
    results = [event for event in json_lines(raw) if event.get("type") == "result"]
    if not results:
        return AgentOutput(text=raw, cost_usd=None, is_error=False, parsed=False)
    return parse_json_result(results[-1], raw)


def event_text(event: Mapping[str, Any]) -> str | None:
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") in ("agent_message", "assistant_message"):
        text = item.get("text")
        return text if isinstance(text, str) else None
    for key in ("text", "content", "message"):
        value = event.get(key)
        if isinstance(value, str) and event.get("type") in ("message", "assistant", "response"):
            return value
    return None


def parse_jsonl(raw: str) -> AgentOutput:
    events = json_lines(raw)
    if not events:
        return AgentOutput(text=raw, cost_usd=None, is_error=False, parsed=False)
    messages = [text for event in events if (text := event_text(event)) is not None]
    failed = any(event.get("type") in ("turn.failed", "error") for event in events)
    tokens_in = tokens_out = None
    for event in events:
        if event.get("type") == "turn.completed":
            tokens_in, tokens_out = usage_tokens(event)
    cost = next(
        (value for event in events if (value := as_float(event.get("total_cost_usd"))) is not None),
        None,
    )
    return AgentOutput(
        text=messages[-1] if messages else raw,
        cost_usd=cost,
        is_error=failed or not messages,
        parsed=bool(messages),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def parse_output(agent: Agent, raw: str) -> AgentOutput:
    clean = strip_ansi(raw)
    if agent.parser == "json":
        return parse_json(clean.strip(), agent)
    if agent.parser == "stream-json":
        return parse_stream_json(clean)
    if agent.parser == "jsonl":
        return parse_jsonl(clean)
    text = clean.strip()
    return AgentOutput(text=text, cost_usd=None, is_error=False, parsed=bool(text))


RATE_LIMIT_WINDOW = 2000


def is_rate_limited(agent: Agent, global_patterns: tuple[str, ...], text: str) -> bool:
    tail = text[-RATE_LIMIT_WINDOW:]
    return any(
        re.search(pattern, tail, re.IGNORECASE)
        for pattern in (*agent.rate_limit_patterns, *global_patterns)
    )
