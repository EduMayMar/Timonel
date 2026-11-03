from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml

from timonel import __version__, gitops
from timonel.classify import KINDS, Classification, schema_errors
from timonel.config import TIERS, Config, ConfigError, user_config_path, validate_text
from timonel.doctor import AgentHealth, diagnose
from timonel.engine import Engine, RunError, RunOptions
from timonel.gitops import GitError
from timonel.ledger import Ledger
from timonel.router import Plan, RoutingError, Step, parse_force
from timonel.stats import spend_since, start_of_day, summary

COOKIE = "timonel_session"
MAX_BODY = 1_000_000
SSE_POLL_S = 0.25
SSE_HEARTBEAT_S = 15
HEALTH_TTL_S = 120
ACTIVE = ("planning", "running")


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.details = details


def page_template() -> str:
    return resources.files("timonel.web").joinpath("index.html").read_text(encoding="utf-8")


def plan_from_client(data: Any, config: Config) -> Plan:
    if not isinstance(data, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "plan must be an object")
    classification = data.get("classification")
    if not isinstance(classification, dict) or schema_errors(classification):
        raise ApiError(
            HTTPStatus.BAD_REQUEST, "invalid classification", schema_errors(classification)
        )
    ladders_raw = data.get("ladders")
    if not isinstance(ladders_raw, list) or not ladders_raw:
        raise ApiError(HTTPStatus.BAD_REQUEST, "plan needs at least one ladder")
    ladders: list[list[Step]] = []
    for ladder in ladders_raw:
        if not isinstance(ladder, list) or not ladder:
            raise ApiError(HTTPStatus.BAD_REQUEST, "each ladder must be a non-empty list")
        steps = []
        for raw in ladder:
            if not isinstance(raw, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "each step must be an object")
            agent = raw.get("agent")
            tier = raw.get("tier")
            if agent not in config.agents:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown agent {agent!r}")
            if tier not in TIERS:
                raise ApiError(HTTPStatus.BAD_REQUEST, "tier must be 1-4")
            model, effort = str(raw.get("model") or ""), str(raw.get("effort") or "")
            if len(model) > 200 or len(effort) > 40:
                raise ApiError(HTTPStatus.BAD_REQUEST, "model or effort too long")
            steps.append(
                Step(
                    int(tier),
                    str(agent),
                    model,
                    effort,
                    config.agents[str(agent)].billing,
                    "edited",
                )
            )
        ladders.append(steps)
    item = Classification.from_dict(
        {**classification, "source": "forced", "reason": "edited in the UI"}
    )
    return Plan(
        classification=item,
        profile=str(data.get("profile") or "balanced"),
        start_tier=ladders[0][0].tier,
        ladders=ladders,
        write=item.needs_write,
        max_escalations=len(ladders) - 1,
        review_min_tier=config.execution.review_min_tier,
        reasons=["route edited in the web UI"],
    )


class App:
    def __init__(
        self,
        config: Config,
        ledger: Ledger,
        repo: Path,
        port: int = 0,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.repo = repo
        self.engine = Engine(config, ledger, environ=environ)
        self.token = secrets.token_urlsafe(32)
        self.session = secrets.token_urlsafe(32)
        self.token_used = False
        self.csrf = secrets.token_urlsafe(32)
        self.port = port
        self.health: tuple[float, list[AgentHealth]] | None = None
        self.health_lock = threading.Lock()
        self.threads: dict[str, threading.Thread] = {}

    @property
    def origins(self) -> set[str]:
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

    @property
    def hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def agent_health(self, refresh: bool = False) -> list[AgentHealth]:
        with self.health_lock:
            if refresh or self.health is None or time.time() - self.health[0] > HEALTH_TTL_S:
                self.health = (time.time(), diagnose(self.config, environ=self.engine.environ))
            return self.health[1]

    def state(self) -> dict[str, Any]:
        ledger = self.engine.ledger
        cooldowns = ledger.cooldowns()
        return {
            "version": __version__,
            "repo": str(self.repo),
            "agents": [
                {**asdict(item), "usable": item.usable, "cooldown_until": cooldowns.get(item.name)}
                for item in self.agent_health()
            ],
            "spend_today_usd": spend_since(ledger, start_of_day()),
            "active": [
                run for status in ACTIVE for run in ledger.list_runs(status=status, limit=50)
            ],
            "recent": ledger.list_runs(limit=8),
            "profiles": sorted(self.config.profiles),
            "kinds": list(KINDS),
            "config_source": str(self.config.source) if self.config.source else None,
        }

    def plan(self, body: dict[str, Any]) -> dict[str, Any]:
        task = body.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "task is required")
        force = body.get("force")
        plan = self.engine.preview(
            task,
            profile=str(body.get("profile") or "balanced"),
            force=parse_force(force) if isinstance(force, str) and force else None,
            cwd=self.repo,
        )
        return plan.to_dict()

    def start_run(self, body: dict[str, Any]) -> dict[str, Any]:
        task = body.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 50_000:
            raise ApiError(HTTPStatus.BAD_REQUEST, "task is required (max 50000 characters)")
        plan = plan_from_client(body["plan"], self.config) if body.get("plan") is not None else None
        options = RunOptions(
            task=task,
            repo=self.repo,
            profile=str(body.get("profile") or "balanced"),
            plan=plan,
            race=bool(body.get("race")),
            decompose=True if body.get("decompose") else None,
        )
        run_id = self.engine.start(options)
        thread = threading.Thread(target=self.engine.run, args=(options, run_id), daemon=True)
        self.threads[run_id] = thread
        thread.start()
        return {"run_id": run_id}

    def run_detail(self, run_id: str) -> dict[str, Any]:
        record = self.engine.require(run_id)
        return {
            **record,
            "attempts": self.engine.ledger.attempts(record["id"]),
            "children": self.engine.ledger.child_runs(record["id"]),
        }

    def routes(self) -> dict[str, Any]:
        return {
            "tiers": {
                str(tier): [asdict(rung) for rung in rungs]
                for tier, rungs in sorted(self.config.tiers.items())
            },
            "agents": {
                name: {
                    "billing": agent.billing,
                    "verified": agent.verified,
                    "models": list(agent.known_models),
                }
                for name, agent in self.config.agents.items()
            },
            "path": str(self.config.source or user_config_path()),
        }

    def save_routes(self, body: dict[str, Any]) -> dict[str, Any]:
        tiers = body.get("tiers")
        if not isinstance(tiers, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "tiers must be an object")
        target = self.config.source or user_config_path()
        existing: dict[str, Any] = {}
        if target.is_file():
            loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        clean: dict[int, list[dict[str, str]]] = {}
        for key, rungs in tiers.items():
            if not isinstance(rungs, list):
                raise ApiError(HTTPStatus.BAD_REQUEST, f"tier {key} must be a list")
            clean[int(key) if str(key).isdigit() else 0] = [
                {
                    "agent": str(rung.get("agent", "")),
                    "model": str(rung.get("model", "")),
                    "effort": str(rung.get("effort", "")),
                }
                for rung in rungs
                if isinstance(rung, dict)
            ]
        document = {
            **existing,
            "tiers": {tier: rungs for tier, rungs in sorted(clean.items()) if rungs},
        }
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        try:
            config = validate_text(text, str(target))
        except ConfigError as error:
            raise ApiError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "invalid routes", error.problems
            ) from error
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            target.with_suffix(".yaml.bak").write_text(
                target.read_text(encoding="utf-8"), encoding="utf-8"
            )
        target.write_text(text, encoding="utf-8")
        self.config = config
        self.engine.config = config
        return {"saved": str(target), "yaml": text}


class Handler(BaseHTTPRequestHandler):
    server_version = "timonel"
    sys_version = ""
    app: App

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def cookie_name(self) -> str:
        return f"{COOKIE}_{self.app.port}"

    def cookie_token(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get(self.cookie_name)
        return morsel.value if morsel else ""

    def authenticated(self) -> bool:
        return hmac.compare_digest(self.cookie_token().encode(), self.app.session.encode())

    def host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.app.hosts

    def security_headers(self, nonce: str | None = None) -> None:
        source = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; script-src {source}; style-src {source}; connect-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def send_json(self, status: HTTPStatus, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status: HTTPStatus, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def reject(self, status: HTTPStatus, message: str) -> None:
        self.send_json(status, {"error": message})

    def guard(self, mutating: bool) -> bool:
        if not self.host_ok():
            self.reject(HTTPStatus.FORBIDDEN, "invalid Host header")
            return False
        if not self.authenticated():
            self.reject(HTTPStatus.UNAUTHORIZED, "missing or invalid session")
            return False
        if mutating:
            if self.headers.get("Origin", "") not in self.app.origins:
                self.reject(HTTPStatus.FORBIDDEN, "cross-origin request refused")
                return False
            if not hmac.compare_digest(
                self.headers.get("X-CSRF-Token", "").encode(), self.app.csrf.encode()
            ):
                self.reject(HTTPStatus.FORBIDDEN, "missing or invalid CSRF token")
                return False
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self.reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
                return False
        return True

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from error
        if length < 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
        if length > MAX_BODY:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid JSON") from error
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "expected a JSON object")
        return data

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/":
            self.serve_index(parse_qs(url.query))
            return
        if url.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if not url.path.startswith("/api/"):
            self.reject(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self.guard(mutating=False):
            return
        self.dispatch("GET", url.path, parse_qs(url.query))

    def do_POST(self) -> None:
        self.handle_mutation("POST")

    def do_PUT(self) -> None:
        self.handle_mutation("PUT")

    def do_DELETE(self) -> None:
        self.reject(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    def do_OPTIONS(self) -> None:
        self.reject(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    def handle_mutation(self, method: str) -> None:
        url = urlsplit(self.path)
        if not self.guard(mutating=True):
            return
        self.dispatch(method, url.path, parse_qs(url.query))

    def serve_index(self, query: dict[str, list[str]]) -> None:
        if not self.host_ok():
            self.reject(HTTPStatus.FORBIDDEN, "invalid Host header")
            return
        offered = query.get("token", [""])[0]
        if offered:
            valid = hmac.compare_digest(offered.encode(), self.app.token.encode())
            if not valid or self.app.token_used:
                self.send_text(
                    HTTPStatus.UNAUTHORIZED,
                    "Invalid or already used token. Restart 'timonel ui' for a new URL.",
                )
                return
            self.app.token_used = True
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                f"{self.cookie_name}={self.app.session}; HttpOnly; SameSite=Strict; Path=/",
            )
            self.security_headers()
            self.end_headers()
            return
        if not self.authenticated():
            self.send_text(
                HTTPStatus.UNAUTHORIZED,
                "Open the URL printed by 'timonel ui' (it contains a one-time token).",
            )
            return
        nonce = secrets.token_urlsafe(16)
        html = (
            page_template()
            .replace("{{NONCE}}", nonce)
            .replace("{{CSRF}}", self.app.csrf)
            .replace("{{VERSION}}", __version__)
        )
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers(nonce)
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        parts = [part for part in path.split("/") if part][1:]
        app = self.app
        try:
            if method == "GET" and parts == ["state"]:
                self.send_json(HTTPStatus.OK, app.state())
            elif method == "POST" and parts == ["doctor"]:
                app.agent_health(refresh=True)
                self.send_json(HTTPStatus.OK, app.state())
            elif method == "POST" and parts == ["plan"]:
                self.send_json(HTTPStatus.OK, app.plan(self.read_json()))
            elif method == "POST" and parts == ["runs"]:
                self.send_json(HTTPStatus.ACCEPTED, app.start_run(self.read_json()))
            elif method == "GET" and parts == ["runs"]:
                status = query.get("status", [""])[0] or None
                search = query.get("q", [""])[0] or None
                self.send_json(
                    HTTPStatus.OK,
                    app.engine.ledger.list_runs(limit=200, status=status, search=search),
                )
            elif method == "GET" and len(parts) == 2 and parts[0] == "runs":
                self.send_json(HTTPStatus.OK, app.run_detail(parts[1]))
            elif method == "GET" and len(parts) == 3 and parts[0] == "runs" and parts[2] == "diff":
                self.send_json(HTTPStatus.OK, {"diff": app.engine.diff(parts[1])})
            elif (
                method == "GET" and len(parts) == 3 and parts[0] == "runs" and parts[2] == "events"
            ):
                self.stream_events(parts[1], query)
            elif (
                method == "POST"
                and len(parts) == 3
                and parts[0] == "runs"
                and parts[2] in ("apply", "discard", "cancel")
            ):
                self.read_json()
                self.run_action(parts[1], parts[2])
            elif method == "GET" and parts == ["stats"]:
                self.send_json(HTTPStatus.OK, summary(app.config, app.engine.ledger))
            elif method == "GET" and parts == ["routes"]:
                self.send_json(HTTPStatus.OK, app.routes())
            elif method == "PUT" and parts == ["routes"]:
                self.send_json(HTTPStatus.OK, app.save_routes(self.read_json()))
            else:
                self.reject(HTTPStatus.NOT_FOUND, "not found")
        except ApiError as error:
            self.send_json(error.status, {"error": str(error), "details": error.details})
        except (RunError, RoutingError, GitError, ValueError) as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": app.engine.redact(str(error))})

    def run_action(self, run_id: str, action: str) -> None:
        engine = self.app.engine
        record = engine.require(run_id)
        if action == "cancel":
            if not engine.cancel(record["id"]):
                raise ApiError(HTTPStatus.CONFLICT, "run is not active in this server")
            self.send_json(HTTPStatus.OK, {"cancelled": record["id"]})
        elif action == "apply":
            self.send_json(HTTPStatus.OK, engine.apply(record["id"]))
        else:
            self.send_json(HTTPStatus.OK, engine.discard(record["id"]))

    def stream_events(self, run_id: str, query: dict[str, list[str]]) -> None:
        record = self.app.engine.require(run_id)
        after_raw = self.headers.get("Last-Event-ID") or query.get("after", ["0"])[0]
        after = int(after_raw) if after_raw.isdigit() else 0
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("X-Accel-Buffering", "no")
        self.security_headers()
        self.end_headers()
        last_beat = time.monotonic()
        try:
            while True:
                events = self.app.engine.ledger.events(record["id"], after)
                for event in events:
                    after = event["id"]
                    payload = json.dumps(
                        {"kind": event["kind"], "ts": event["ts"], "data": event["data"]},
                        ensure_ascii=False,
                    )
                    self.wfile.write(
                        f"id: {after}\nevent: {event['kind']}\ndata: {payload}\n\n".encode()
                    )
                if events:
                    self.wfile.flush()
                current = self.app.engine.ledger.get_run(record["id"]) or {}
                if current.get("status") not in ACTIVE and not events:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                if time.monotonic() - last_beat > SSE_HEARTBEAT_S:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    last_beat = time.monotonic()
                time.sleep(SSE_POLL_S)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def make_server(app: App, port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    app.port = server.server_address[1]
    return server


def serve(
    config: Config, port: int = 0, open_browser: bool = True, repo: Path | None = None
) -> int:
    try:
        root = gitops.repo_root(repo or Path.cwd())
    except GitError:
        root = (repo or Path.cwd()).resolve()
    app = App(config, Ledger(), root)
    server = make_server(app, port)
    url = f"http://127.0.0.1:{app.port}/?token={app.token}"
    print(f"Timonel UI for {root}\n  {url}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for run_id in list(app.engine.cancels):
            app.engine.cancel(run_id)
        for thread in app.threads.values():
            thread.join(10)
    finally:
        server.server_close()
    return 0
