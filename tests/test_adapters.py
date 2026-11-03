import json

import pytest

from timonel.adapters import (
    SecretMissingError,
    build_env,
    build_invocation,
    is_rate_limited,
    parse_output,
    render_argv,
    strip_ansi,
)
from timonel.config import Rung, validate_text

CONFIG = validate_text("agents:\n  claude:\n    allow_dangerous: true\n")
AGENTS = CONFIG.agents


def test_claude_argv_uses_stdin_and_skips_empty_groups() -> None:
    argv = render_argv(AGENTS["claude"], Rung("claude", "opus", ""), "hi", write=True, timeout_s=60)
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--permission-mode") : argv.index("--permission-mode") + 2] == [
        "--permission-mode",
        "acceptEdits",
    ]
    assert "--effort" not in argv
    assert argv[-2:] == ["--model", "opus"]
    assert "hi" not in argv


def test_read_only_and_effort() -> None:
    argv = render_argv(AGENTS["codex"], Rung("codex", "", "high"), "x", write=False, timeout_s=60)
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") : argv.index("--sandbox") + 2] == ["--sandbox", "read-only"]
    assert 'model_reasoning_effort="high"' in argv
    assert "--model" not in argv
    assert argv[-1] == "-"


def test_prompt_is_inserted_literally_once() -> None:
    prompt = "use {model} and {effort} literally; $(rm -rf /) && echo"
    argv = render_argv(
        AGENTS["copilot"], Rung("copilot", "auto", "low"), prompt, write=False, timeout_s=5
    )
    assert argv[argv.index("-p") + 1] == prompt


def test_timeout_placeholder_and_dangerous_flags() -> None:
    argv = render_argv(AGENTS["agy"], Rung("agy"), "x", write=True, timeout_s=42)
    assert "42s" in argv
    safe = render_argv(AGENTS["claude"], Rung("claude"), "x", write=True, timeout_s=1)
    risky = render_argv(
        AGENTS["claude"], Rung("claude"), "x", write=True, timeout_s=1, dangerous=True
    )
    assert "--dangerously-skip-permissions" not in safe
    assert "--dangerously-skip-permissions" in risky
    blocked = render_argv(
        AGENTS["codex"], Rung("codex"), "x", write=True, timeout_s=1, dangerous=True
    )
    assert not any("dangerously" in token for token in blocked)


def test_secrets_only_reach_their_own_agent() -> None:
    environ = {"PATH": "/bin", "DEEPSEEK_API_KEY": "sk-secret", "ANTHROPIC_API_KEY": "anthropic"}
    deepseek = build_env(AGENTS["deepseek"], AGENTS, environ)
    assert deepseek["ANTHROPIC_AUTH_TOKEN"] == "sk-secret"
    assert deepseek["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert "ANTHROPIC_API_KEY" not in deepseek
    assert "DEEPSEEK_API_KEY" not in deepseek
    claude = build_env(AGENTS["claude"], AGENTS, environ)
    assert "DEEPSEEK_API_KEY" not in claude
    assert "ANTHROPIC_AUTH_TOKEN" not in claude
    assert claude["ANTHROPIC_API_KEY"] == "anthropic"


def test_missing_secret_is_explicit() -> None:
    with pytest.raises(SecretMissingError, match="DEEPSEEK_API_KEY"):
        build_env(AGENTS["deepseek"], AGENTS, {"PATH": "/bin"})


def test_invocation_routes_prompt() -> None:
    claude = build_invocation(
        AGENTS["claude"], Rung("claude"), "task", AGENTS, write=False, timeout_s=9, environ={}
    )
    assert claude.stdin == "task"
    copilot = build_invocation(
        AGENTS["copilot"], Rung("copilot"), "task", AGENTS, write=False, timeout_s=9, environ={}
    )
    assert copilot.stdin is None
    assert "task" in copilot.argv
    agy = build_invocation(
        AGENTS["agy"], Rung("agy"), "task", AGENTS, write=False, timeout_s=9, environ={}
    )
    assert agy.pty is True


def test_parse_claude_json() -> None:
    raw = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "total_cost_usd": 0.5,
            "usage": {"input_tokens": 3, "output_tokens": 4},
        }
    )
    output = parse_output(AGENTS["claude"], raw)
    assert (output.text, output.cost_usd, output.is_error, output.parsed) == (
        "ok",
        0.5,
        False,
        True,
    )
    assert (output.tokens_in, output.tokens_out) == (3, 4)


@pytest.mark.parametrize(
    "raw", ['{"result": ', "not json", "[1, 2]", '{"subtype": "error_max_turns", "result": "x"}']
)
def test_parse_claude_json_failures(raw: str) -> None:
    assert parse_output(AGENTS["claude"], raw).is_error


def test_parse_stream_json_uses_last_result() -> None:
    agent = validate_text("agents:\n  claude:\n    parser: stream-json\n").agents["claude"]
    raw = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            "garbage line",
            json.dumps(
                {"type": "result", "subtype": "success", "result": "final", "total_cost_usd": 0.1}
            ),
        ]
    )
    output = parse_output(agent, raw)
    assert output.text == "final"
    assert output.cost_usd == 0.1
    assert parse_output(agent, json.dumps({"type": "assistant"})).is_error


def test_parse_codex_jsonl() -> None:
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "1"}),
            json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "hmm"}}),
            json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}
            ),
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}
            ),
        ]
    )
    output = parse_output(AGENTS["codex"], raw)
    assert (output.text, output.is_error, output.tokens_in) == ("done", False, 10)
    failed = parse_output(AGENTS["codex"], raw + "\n" + json.dumps({"type": "turn.failed"}))
    assert failed.is_error
    assert parse_output(AGENTS["codex"], "plain text").is_error


def test_parse_text_strips_ansi() -> None:
    output = parse_output(AGENTS["agy"], "\x1b[32mhello\x1b[0m\r\n")
    assert output.text == "hello"
    assert parse_output(AGENTS["agy"], "  \n").is_error
    assert strip_ansi("a\x1b]0;title\x07b\rc") == "ab\nc"


def test_rate_limit_patterns() -> None:
    patterns = CONFIG.rate_limit_patterns
    assert is_rate_limited(AGENTS["codex"], patterns, "HTTP 429 Too Many Requests")
    assert is_rate_limited(AGENTS["claude"], patterns, "Claude AI usage limit reached|1700000000")
    assert is_rate_limited(AGENTS["deepseek"], patterns, "Insufficient Balance")
    assert not is_rate_limited(AGENTS["codex"], patterns, "SyntaxError on line 4290")
