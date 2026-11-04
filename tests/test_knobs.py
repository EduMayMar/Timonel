import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from timonel.classify import Classification
from timonel.engine import Engine, RunOptions, detect_verify
from timonel.ledger import Ledger
from timonel.router import RoutingError, build_plan

from .conftest import Fakes, make_config

SIMPLE = json.dumps(
    {"complexity": 1, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
)
CHECK_GOOD = (
    (
        sys.executable,
        "-c",
        "import pathlib,sys; sys.exit(pathlib.Path('result.txt').read_text()!='good')",
    ),
)


def engine_with(fakes: Fakes, extra: str = "", **env: str) -> Engine:
    return Engine(
        make_config(extra), Ledger(), environ={**fakes.env, "FAKE_CLASSIFICATION": SIMPLE, **env}
    )


def executes(engine: Engine, run_id: str) -> list[tuple[str, str]]:
    return [
        (row["agent"], row["outcome"])
        for row in engine.ledger.attempts(run_id)
        if row["role"] == "execute"
    ]


class FakeApi(BaseHTTPRequestHandler):
    replies: list[tuple[int, dict[str, Any]]] = []
    seen: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeApi.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        status, payload = FakeApi.replies.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def api() -> Iterator[str]:
    FakeApi.replies, FakeApi.seen = [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_openai_compatible_provider_classifies(fakes: Fakes, repo: Path, api: str) -> None:
    answer = {
        "complexity": 1,
        "kind": "explain",
        "risk": "low",
        "needs_write": False,
        "estimated_files": 0,
    }
    FakeApi.replies = [
        (
            200,
            {
                "choices": [{"message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )
    ]
    extra = f"agents:\n  local:\n    kind: provider\n    command: local\n    provider: {{api: openai, base_url: '{api}/v1', key_env: LOCAL_KEY}}\nclassifier:\n  ladder:\n    - {{agent: local, model: tiny}}\n"
    engine = engine_with(fakes, extra, LOCAL_KEY="sk-local-secret-value-1")
    record = engine.run(RunOptions(task="[[say:ok]] what does app.py do", repo=repo))
    assert record["classification"]["source"] == "model"
    seen = FakeApi.seen[0]
    assert seen["path"] == "/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-local-secret-value-1"
    assert seen["body"]["model"] == "tiny"
    classify_row = engine.ledger.attempts(record["id"])[0]
    assert (classify_row["agent"], classify_row["tokens_in"]) == ("local", 7)


def test_anthropic_provider_reviews_and_rate_limits(fakes: Fakes, repo: Path, api: str) -> None:
    verdict = json.dumps({"approve": False, "issues": ["bad"]})
    FakeApi.replies = [
        (429, {"error": "slow down"}),
        (200, {"content": [{"type": "text", "text": verdict}]}),
    ]
    extra = (
        f"agents:\n  a1:\n    kind: provider\n    command: a1\n    provider: {{api: anthropic, base_url: '{api}', key_env: A_KEY}}\n"
        f"  a2:\n    kind: provider\n    command: a2\n    provider: {{api: anthropic, base_url: '{api}', key_env: A_KEY}}\n"
        "review:\n  reviewers:\n    - {agent: a1, model: m}\n    - {agent: a2, model: m}\n  max_rounds: 0\n"
    )
    engine = engine_with(
        fakes,
        extra,
        A_KEY="anthropic-key-123456",
        FAKE_CLASSIFICATION=json.dumps(
            {
                "complexity": 3,
                "kind": "edit",
                "risk": "low",
                "needs_write": True,
                "estimated_files": 1,
            }
        ),
    )
    record = engine.run(RunOptions(task="[[edit claude:result.txt=good]] go", repo=repo))
    first = next(row for row in engine.ledger.attempts(record["id"]) if row["role"] == "execute")
    assert first["outcome"] == "verify_failed"
    assert "review rejected the change" in first["error"]
    assert FakeApi.seen[0]["path"] == "/v1/messages"
    assert FakeApi.seen[0]["headers"]["X-Api-Key"] == "anthropic-key-123456"
    assert FakeApi.seen[0]["headers"]["Anthropic-Version"] == "2023-06-01"
    assert FakeApi.seen[0]["body"]["max_tokens"] == 2048
    assert "a1" in engine.ledger.cooldowns()


def test_provider_without_key_or_model_is_a_launch_error(fakes: Fakes, repo: Path) -> None:
    from timonel.providers import ProviderError, call_provider

    config = make_config()
    with pytest.raises(ProviderError, match="needs a model"):
        call_provider(config.agents["openai"], "", "hi", {})
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        call_provider(config.agents["openai"], "gpt", "hi", {})


def test_prompt_via_file_is_written_and_removed(fakes: Fakes, repo: Path) -> None:
    extra = (
        "agents:\n  copilot:\n    prompt_via: file\n    prompt_args: ['--file', '{prompt_file}']\n"
    )
    engine = engine_with(fakes, extra)
    plan = build_plan(
        engine.config, Classification(1, "explain", "low", False, 0), available={"copilot"}
    )
    record = engine.run(RunOptions(task="[[say:from file]] explain", repo=repo, plan=plan))
    assert record["summary"] == "from file"
    call = [c for c in fakes.calls() if c["name"] == "copilot"][-1]
    prompt_path = Path(call["argv"][call["argv"].index("--file") + 1])
    assert not prompt_path.exists()
    assert "from file" in call["prompt"]


def test_rules_force_deny_and_verify(fakes: Fakes, repo: Path) -> None:
    extra = (
        "routing:\n  deny_models: ['claude:opus']\n  rules:\n"
        "    - name: docs\n      when: {paths: ['*.md']}\n      then: {force: 'codex:o3'}\n"
        "    - name: py\n      when: {languages: [python]}\n      then: {deny_agents: [copilot], verify: [[python, -c, 'pass']]}\n"
    )
    config = make_config(extra)
    plan = build_plan(config, Classification(1, "edit", "low", True, 1), task="update README.md")
    assert plan.forced and plan.steps()[0].label == "codex:o3"
    assert plan.rules == ["docs"]
    plan = build_plan(config, Classification(1, "edit", "low", True, 1), task="fix app.py")
    assert all(step.agent != "copilot" for step in plan.steps())
    assert plan.verify == [["python", "-c", "pass"]]
    plan = build_plan(config, Classification(4, "edit", "low", True, 1), task="x")
    assert all(step.label != "claude:opus" for step in plan.steps())
    assert any("deny_models" in reason for reason in plan.skipped)


def test_write_kinds_and_read_only_support(fakes: Fakes) -> None:
    config = make_config(
        "execution:\n  write_kinds: [edit]\nagents:\n  codex:\n    supports_read_only: false\n"
    )
    plan = build_plan(config, Classification(2, "debug", "low", True, 1))
    assert plan.write is False
    assert all(step.agent != "codex" for step in plan.steps())
    with pytest.raises(RoutingError, match="cannot run read-only"):
        build_plan(
            config,
            Classification(2, "explain", "low", False, 0),
            force=__import__("timonel.config", fromlist=["Rung"]).Rung("codex"),
        )


def test_disabled_and_denied_agents_are_never_planned() -> None:
    config = make_config(
        "agents:\n  codex:\n    enabled: false\nrouting:\n  deny_agents: [copilot]\nrouting_placeholder: 1\n".replace(
            "routing_placeholder: 1\n", ""
        )
    )
    plan = build_plan(config, Classification(2, "edit", "low", True, 1))
    assert {step.agent for step in plan.steps()}.isdisjoint({"codex", "copilot"})


def test_budget_skip_and_stop(fakes: Fakes, repo: Path) -> None:
    engine = engine_with(
        fakes, "budgets:\n  - {agent: copilot, period: day, max_requests: 0, action: skip}\n"
    )
    record = engine.run(
        RunOptions(task="[[edit all:result.txt=good]] go", repo=repo, verify=CHECK_GOOD)
    )
    assert record["status"] == "succeeded"
    assert "copilot" not in [agent for agent, _ in executes(engine, record["id"])]
    assert any(event["kind"] == "budget" for event in engine.ledger.events(record["id"]))
    stopper = engine_with(fakes, "budgets:\n  - {period: week, max_usd: 0, action: stop}\n")
    stopped = stopper.run(RunOptions(task="[[edit all:result.txt=good]] go", repo=repo))
    assert stopped["status"] == "failed" and "budget" in stopped["error"]
    asker = engine_with(fakes, "budgets:\n  - {period: month, max_tokens: 0, action: ask}\n")
    asker.confirm = lambda question: True
    assert (
        asker.run(RunOptions(task="[[edit all:result.txt=good]] go", repo=repo))["status"]
        == "succeeded"
    )


def test_hooks_run_and_pre_run_failure_stops(fakes: Fakes, repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "hooks.txt"
    write = f"import os; open(r'{marker}', 'a').write(os.environ['TIMONEL_HOOK'] + chr(10))"
    command = [sys.executable, "-c", write]
    engine = engine_with(
        fakes, "hooks: " + json.dumps({"pre_run": [command], "post_run": [command]}) + "\n"
    )
    record = engine.run(RunOptions(task="[[edit all:result.txt=good]] go", repo=repo))
    assert record["status"] == "succeeded"
    assert marker.read_text().split() == ["pre_run", "post_run"]
    failing = engine_with(
        fakes,
        "hooks: " + json.dumps({"pre_run": [[sys.executable, "-c", "raise SystemExit(3)"]]}) + "\n",
    )
    record = failing.run(RunOptions(task="go", repo=repo))
    assert record["status"] == "failed" and "pre_run hook failed" in record["error"]


def test_webhook_notification_has_no_task_text(fakes: Fakes, repo: Path, api: str) -> None:
    FakeApi.replies = [(200, {})]
    engine = engine_with(
        fakes, f"notifications:\n  webhook_url: '{api}/hook'\n  events: [succeeded]\n"
    )
    record = engine.run(RunOptions(task="[[edit all:result.txt=good]] secret task text", repo=repo))
    assert FakeApi.seen[0]["body"] == {
        "id": record["id"],
        "status": "succeeded",
        "diff_stat": record["diff_stat"],
    }


def test_escalation_policies_and_review_rejection(fakes: Fakes, repo: Path) -> None:
    engine = engine_with(
        fakes, "escalation:\n  on_verify_failure: next_rung\n  context_attempts: 0\n"
    )
    task = "[[edit copilot:result.txt=bad]] [[edit claude:result.txt=good]] go"
    record = engine.run(RunOptions(task=task, repo=repo, verify=CHECK_GOOD))
    assert executes(engine, record["id"])[:2] == [
        ("copilot", "verify_failed"),
        ("claude", "succeeded"),
    ]
    assert all("failed verification" not in str(c["prompt"]) for c in fakes.calls())
    strict = engine_with(
        fakes,
        "review:\n  on_no_verdict: reject\n  reviewers: []\n",
        FAKE_REVIEW="nope",
        FAKE_CLASSIFICATION=json.dumps(
            {
                "complexity": 4,
                "kind": "edit",
                "risk": "low",
                "needs_write": True,
                "estimated_files": 1,
            }
        ),
    )
    plan = build_plan(
        strict.config, Classification(4, "edit", "low", True, 1), available={"claude", "codex"}
    )
    record = strict.run(RunOptions(task="[[edit claude:result.txt=good]] go", repo=repo, plan=plan))
    assert record["status"] == "failed"


def test_verification_autodetect_and_exit_codes(fakes: Fakes, repo: Path) -> None:
    (repo / "go.mod").write_text("module x\n", encoding="utf-8")
    config = make_config("verification:\n  autodetect: true\n")
    assert detect_verify(repo, config) == (("go", "test", "./..."),)
    engine = engine_with(
        fakes,
        "verification:\n  commands: [[python, -c, 'raise SystemExit(5)']]\n  success_exit_codes: [0, 5]\n",
    )
    record = engine.run(RunOptions(task="[[edit all:result.txt=good]] go", repo=repo))
    assert record["status"] == "succeeded"


def test_branch_pattern_and_worktree_dir(fakes: Fakes, repo: Path, tmp_path: Path) -> None:
    folder = tmp_path / "trees"
    engine = engine_with(
        fakes,
        f"execution:\n  branch_pattern: 'ai/{{id}}'\n  worktree_dir: '{folder.as_posix()}'\n  cleanup_on_apply: false\n",
    )
    record = engine.run(RunOptions(task="[[edit all:result.txt=good]] go", repo=repo))
    assert record["branch"] == f"ai/{record['id']}"
    assert Path(record["worktree"]).is_relative_to(folder)
    engine.apply(record["id"])
    assert Path(record["worktree"]).exists()


def test_outputs_not_stored_when_privacy_says_so(fakes: Fakes, repo: Path) -> None:
    engine = engine_with(fakes, "privacy:\n  store_outputs: false\n")
    record = engine.run(
        RunOptions(task="[[say:visible]] [[edit all:result.txt=good]] go", repo=repo)
    )
    assert all(not row["output"] for row in engine.ledger.attempts(record["id"]))


def test_custom_redaction_patterns(fakes: Fakes, repo: Path) -> None:
    engine = engine_with(fakes, "privacy:\n  redact_patterns: ['INTERNAL-[0-9]+']\n")
    plan = build_plan(
        engine.config, Classification(1, "explain", "low", False, 0), available={"codex"}
    )
    record = engine.run(
        RunOptions(task="[[say:ticket INTERNAL-4242]] explain", repo=repo, plan=plan)
    )
    assert record["summary"] == "ticket [REDACTED]"
