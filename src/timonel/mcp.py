from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import IO, Any

from timonel import __version__, gitops
from timonel.config import Config
from timonel.engine import Engine, RunError, RunOptions
from timonel.gitops import GitError
from timonel.ledger import Ledger, open_ledger
from timonel.router import RoutingError, parse_force

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TASK_SCHEMA = {"type": "string", "description": "The coding task in natural language"}
REPO_SCHEMA = {
    "type": "string",
    "description": "Path inside the git repository (default: server cwd)",
}
PROFILE_SCHEMA = {"type": "string", "enum": ["cheap", "balanced", "max"], "default": "balanced"}
TOOLS = [
    {
        "name": "timonel_plan",
        "description": "Classify a coding task and show which agent/model ladder Timonel would use, without running it.",
        "inputSchema": {
            "type": "object",
            "properties": {"task": TASK_SCHEMA, "repo": REPO_SCHEMA, "profile": PROFILE_SCHEMA},
            "required": ["task"],
        },
    },
    {
        "name": "timonel_run",
        "description": (
            "Start a task in the background. Write tasks run in an isolated git worktree on branch "
            "timonel/<id>; nothing is merged until a human runs 'timonel apply'. Returns the run id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": TASK_SCHEMA,
                "repo": REPO_SCHEMA,
                "profile": PROFILE_SCHEMA,
                "force": {"type": "string", "description": "agent or agent:model"},
                "race": {"type": "boolean", "default": False},
            },
            "required": ["task"],
        },
    },
    {
        "name": "timonel_status",
        "description": "Show a run's status, attempts and result, or list recent runs when run_id is omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
        },
    },
]


class McpServer:
    def __init__(
        self, config: Config, ledger: Ledger | None = None, cwd: Path | None = None
    ) -> None:
        self.engine = Engine(config, ledger or open_ledger(config.paths.ledger))
        self.cwd = cwd or Path.cwd()
        try:
            self.root: Path | None = gitops.repo_root(self.cwd)
        except gitops.GitError:
            self.root = None
        self.threads: list[threading.Thread] = []

    def repo(self, arguments: dict[str, Any]) -> Path:
        value = arguments.get("repo")
        path = Path(value) if isinstance(value, str) and value else self.cwd
        root = gitops.repo_root(path if path.is_absolute() else self.cwd / path)
        if self.root is None or root != self.root:
            raise ValueError(
                "this MCP server only works on the repository it was started in"
                f" ({self.root or 'none: start timonel mcp inside a git repository'})"
            )
        return root

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        task = arguments.get("task")
        if name in ("timonel_plan", "timonel_run") and (
            not isinstance(task, str) or not task.strip()
        ):
            raise ValueError("task must be a non-empty string")
        profile = str(arguments.get("profile") or "balanced")
        if name == "timonel_plan":
            return self.engine.preview(
                str(task), profile=profile, cwd=self.repo(arguments)
            ).to_dict()
        if name == "timonel_run":
            force = arguments.get("force")
            options = RunOptions(
                task=str(task),
                repo=self.repo(arguments),
                profile=profile,
                force=parse_force(force) if isinstance(force, str) and force else None,
                race=bool(arguments.get("race", False)),
            )
            run_id = self.engine.start(options)
            thread = threading.Thread(target=self.engine.run, args=(options, run_id), daemon=True)
            thread.start()
            self.threads.append(thread)
            return {
                "run_id": run_id,
                "status": "started",
                "next": "call timonel_status with this run_id",
            }
        if name == "timonel_status":
            wanted = arguments.get("run_id")
            if not wanted:
                return {"runs": [summarize(run) for run in self.engine.ledger.list_runs(limit=10)]}
            record = self.engine.require(str(wanted))
            attempts = [
                {
                    key: row[key]
                    for key in (
                        "seq",
                        "role",
                        "tier",
                        "agent",
                        "model",
                        "outcome",
                        "duration_s",
                        "cost_usd",
                    )
                }
                for row in self.engine.ledger.attempts(record["id"])
            ]
            return {
                **summarize(record),
                "summary": record.get("summary"),
                "error": record.get("error"),
                "attempts": attempts,
            }
        raise ValueError(f"unknown tool {name}")

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        ident = message.get("id")
        params = message.get("params") or {}
        if ident is None:
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            return reply(
                ident,
                {
                    "protocolVersion": requested
                    if requested in PROTOCOL_VERSIONS
                    else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "timonel", "version": __version__},
                },
            )
        if method == "ping":
            return reply(ident, {})
        if method == "tools/list":
            return reply(ident, {"tools": TOOLS})
        if method == "tools/call":
            name = str(params.get("name", ""))
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return failure(ident, -32602, "arguments must be an object")
            try:
                result = self.call_tool(name, arguments)
            except (ValueError, RunError, RoutingError, GitError) as error:
                message_text = self.engine.redact(str(error))
                return reply(
                    ident, {"content": [{"type": "text", "text": message_text}], "isError": True}
                )
            text = json.dumps(result, ensure_ascii=False, default=str, indent=2)
            return reply(
                ident, {"content": [{"type": "text", "text": text}], "structuredContent": result}
            )
        return failure(ident, -32601, f"method not found: {method}")

    def safe_handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self.handle(message)
        except Exception as error:
            text = self.engine.redact(f"{type(error).__name__}: {error}")
            return failure(message.get("id"), -32603, f"internal error: {text}")

    def serve(self, reader: IO[str], writer: IO[str]) -> None:
        for line in reader:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response: dict[str, Any] | None = failure(None, -32700, "parse error")
            else:
                response = (
                    self.safe_handle(message)
                    if isinstance(message, dict)
                    else failure(None, -32600, "invalid request")
                )
            if response is not None:
                writer.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
                writer.flush()


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    return {key: run.get(key) for key in ("id", "status", "task", "profile", "branch", "diff_stat")}


def reply(ident: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def failure(ident: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def serve_stdio(config: Config) -> int:
    McpServer(config).serve(sys.stdin, sys.stdout)
    return 0
