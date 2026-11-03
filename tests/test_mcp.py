import io
import json
import time
from pathlib import Path

from timonel.engine import Engine
from timonel.ledger import Ledger
from timonel.mcp import McpServer

from .conftest import Fakes, make_config


def server(fakes: Fakes, repo: Path, ledger: Ledger) -> McpServer:
    instance = McpServer(make_config(), ledger, cwd=repo)
    instance.engine = Engine(
        make_config(),
        ledger,
        environ={
            **fakes.env,
            "FAKE_CLASSIFICATION": json.dumps(
                {
                    "complexity": 1,
                    "kind": "edit",
                    "risk": "low",
                    "needs_write": True,
                    "estimated_files": 1,
                }
            ),
        },
    )
    return instance


def exchange(instance: McpServer, *messages: object) -> list[dict[str, object]]:
    reader = io.StringIO(
        "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    )
    writer = io.StringIO()
    instance.serve(reader, writer)
    return [json.loads(line) for line in writer.getvalue().splitlines()]


def call(ident: int, name: str, arguments: object) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_handshake_and_tool_list(fakes: Fakes, repo: Path, ledger: Ledger) -> None:
    replies = exchange(
        server(fakes, repo, ledger),
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "nope"},
        "not json",
        "[1]",
        "",
    )
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"  # type: ignore[index]
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == [
        "timonel_plan",
        "timonel_run",
        "timonel_status",
    ]  # type: ignore[index]
    assert replies[2]["result"] == {}
    assert replies[3]["error"]["code"] == -32601  # type: ignore[index]
    assert replies[4]["error"]["code"] == -32700  # type: ignore[index]
    assert replies[5]["error"]["code"] == -32600  # type: ignore[index]
    unknown = exchange(
        server(fakes, repo, ledger),
        {"jsonrpc": "2.0", "id": 9, "method": "initialize", "params": {"protocolVersion": "1999"}},
    )
    assert unknown[0]["result"]["protocolVersion"] == "2025-06-18"  # type: ignore[index]


def test_plan_run_status(fakes: Fakes, repo: Path, ledger: Ledger) -> None:
    instance = server(fakes, repo, ledger)
    plan = exchange(instance, call(1, "timonel_plan", {"task": "fix typo in README.md"}))[0]
    assert plan["result"]["structuredContent"]["start_tier"] == 1  # type: ignore[index]
    started = exchange(
        instance, call(2, "timonel_run", {"task": "[[edit all:r.txt=1]] add r", "repo": str(repo)})
    )[0]
    run_id = started["result"]["structuredContent"]["run_id"]  # type: ignore[index]
    for thread in instance.threads:
        thread.join(30)
    status = exchange(instance, call(3, "timonel_status", {"run_id": run_id}))[0]
    content = status["result"]["structuredContent"]  # type: ignore[index]
    assert content["status"] == "succeeded"
    assert content["attempts"]
    listing = exchange(instance, call(4, "timonel_status", {}))[0]
    assert listing["result"]["structuredContent"]["runs"][0]["id"] == run_id  # type: ignore[index]


def test_tool_errors_are_reported_not_raised(
    fakes: Fakes, repo: Path, ledger: Ledger, tmp_path: Path
) -> None:
    instance = server(fakes, repo, ledger)
    replies = exchange(
        instance,
        call(1, "timonel_plan", {"task": ""}),
        call(2, "timonel_plan", {"task": "x", "repo": str(tmp_path)}),
        call(3, "timonel_status", {"run_id": "missing"}),
        call(4, "nope", {}),
        call(5, "timonel_run", "x"),
        call(6, "timonel_plan", {"task": "x", "profile": "ultra"}),
    )
    assert all(reply["result"]["isError"] for reply in replies[:4] + replies[5:])  # type: ignore[index]
    assert replies[4]["error"]["code"] == -32602  # type: ignore[index]
    time.sleep(0)
