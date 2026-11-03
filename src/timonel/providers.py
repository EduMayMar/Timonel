from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from timonel.adapters import AgentOutput, as_int, lookup_secret
from timonel.config import Agent

ANTHROPIC_VERSION = "2023-06-01"
ERROR_BODY_LIMIT = 2000


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class ProviderReply:
    output: AgentOutput
    status: int
    duration_s: float
    rate_limited: bool = False


def endpoint(agent: Agent) -> str:
    if agent.provider is None:
        raise ProviderError(f"{agent.name} is not a provider")
    base = agent.provider.base_url.rstrip("/")
    if agent.provider.api == "anthropic":
        return f"{base}/v1/messages"
    return f"{base}/chat/completions"


def request_body(agent: Agent, model: str, prompt: str) -> dict[str, Any]:
    if agent.provider is None:
        raise ProviderError(f"{agent.name} is not a provider")
    messages = [{"role": "user", "content": prompt}]
    if agent.provider.api == "anthropic":
        return {"model": model, "max_tokens": agent.provider.max_tokens, "messages": messages}
    return {"model": model, "messages": messages}


def request_headers(agent: Agent, environ: Mapping[str, str]) -> dict[str, str]:
    if agent.provider is None:
        raise ProviderError(f"{agent.name} is not a provider")
    provider = agent.provider
    headers = {"Content-Type": "application/json", "User-Agent": "timonel", **provider.headers}
    key = lookup_secret(provider.key_env, environ) if provider.key_env else None
    if key is None and provider.key_required:
        raise ProviderError(
            f"{agent.name} needs {provider.key_env} (environment variable or keyring 'timonel')"
        )
    if key is not None and provider.api == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    elif key is not None:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def read_reply(agent: Agent, payload: Any) -> AgentOutput:
    if agent.provider is None:
        raise ProviderError(f"{agent.name} is not a provider")
    if not isinstance(payload, dict):
        return AgentOutput(
            text=json.dumps(payload)[:ERROR_BODY_LIMIT], cost_usd=None, is_error=True, parsed=False
        )
    raw_usage = payload.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    if agent.provider.api == "anthropic":
        raw_blocks = payload.get("content")
        blocks: list[Any] = raw_blocks if isinstance(raw_blocks, list) else []
        text = "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        tokens_in, tokens_out = (
            as_int(usage.get("input_tokens")),
            as_int(usage.get("output_tokens")),
        )
    else:
        raw_choices = payload.get("choices")
        choices: list[Any] = raw_choices if isinstance(raw_choices, list) else []
        message: Any = (
            choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        )
        content = message.get("content") if isinstance(message, dict) else None
        text = content if isinstance(content, str) else ""
        tokens_in, tokens_out = (
            as_int(usage.get("prompt_tokens")),
            as_int(usage.get("completion_tokens")),
        )
    return AgentOutput(
        text=text,
        cost_usd=None,
        is_error=not text,
        parsed=bool(text),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def call_provider(
    agent: Agent, model: str, prompt: str, environ: Mapping[str, str]
) -> ProviderReply:
    if agent.provider is None:
        raise ProviderError(f"{agent.name} is not a provider")
    if not model:
        raise ProviderError(
            f"{agent.name} needs a model in the ladder (provider connectors have no default model)"
        )
    request = urllib.request.Request(
        endpoint(agent),
        data=json.dumps(request_body(agent, agent.model_name(model), prompt)).encode("utf-8"),
        headers=request_headers(agent, environ),
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=agent.provider.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
            status = int(response.status)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:ERROR_BODY_LIMIT]
        output = AgentOutput(
            text=f"HTTP {error.code}: {body}", cost_usd=None, is_error=True, parsed=False
        )
        return ProviderReply(
            output, error.code, time.monotonic() - started, rate_limited=error.code == 429
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        output = AgentOutput(
            text=f"request failed: {error}", cost_usd=None, is_error=True, parsed=False
        )
        return ProviderReply(output, 0, time.monotonic() - started)
    return ProviderReply(read_reply(agent, payload), status, time.monotonic() - started)
