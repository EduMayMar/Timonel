import http.client
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from timonel.ledger import Ledger
from timonel.web.server import App, make_server

from .conftest import Fakes, make_config

SIMPLE = json.dumps(
    {"complexity": 1, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
)


@pytest.fixture
def app(fakes: Fakes, repo: Path, ledger: Ledger, home: Path) -> Iterator[App]:
    instance = App(
        make_config(), ledger, repo, environ={**fakes.env, "FAKE_CLASSIFICATION": SIMPLE}
    )
    server = make_server(instance)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield instance
    for run_id in list(instance.engine.cancels):
        instance.engine.cancel(run_id)
    for thread in instance.threads.values():
        thread.join(30)
    server.shutdown()
    server.server_close()


class Client:
    def __init__(self, app: App) -> None:
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        body: object = None,
        *,
        host: str | None = None,
        cookie: bool = True,
        origin: str | None = "same",
        csrf: bool = True,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        headers = {"Host": host or f"127.0.0.1:{self.app.port}"}
        if cookie:
            headers["Cookie"] = f"timonel_session_{self.app.port}={self.app.session}"
        if method != "GET":
            if origin == "same":
                headers["Origin"] = f"http://127.0.0.1:{self.app.port}"
            elif origin:
                headers["Origin"] = origin
            if csrf:
                headers["X-CSRF-Token"] = self.app.csrf
            headers["Content-Type"] = content_type
        payload = (
            json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        )
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = response.status, {k.lower(): v for k, v in response.getheaders()}, data
        connection.close()
        return result

    def json(self, method: str, path: str, body: object = None) -> tuple[int, object]:
        status, _, data = self.request(method, path, body)
        return status, json.loads(data or b"null")


def test_token_login_sets_strict_cookie_and_strips_token(app: App) -> None:
    client = Client(app)
    status, headers, _ = client.request("GET", f"/?token={app.token}", cookie=False)
    assert status == 303
    assert headers["location"] == "/"
    assert headers["set-cookie"].startswith(f"timonel_session_{app.port}={app.session};")
    assert app.token not in headers["set-cookie"]
    assert "HttpOnly" in headers["set-cookie"] and "SameSite=Strict" in headers["set-cookie"]
    assert client.request("GET", "/?token=wrong", cookie=False)[0] == 401
    assert client.request("GET", f"/?token={app.token}", cookie=False)[0] == 401
    assert client.request("GET", "/", cookie=False)[0] == 401
    status, headers, body = client.request("GET", "/")
    assert status == 200
    csp = headers["content-security-policy"]
    assert "script-src 'nonce-" in csp and "frame-ancestors 'none'" in csp
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "access-control-allow-origin" not in headers
    assert app.csrf.encode() in body
    assert b"{{NONCE}}" not in body


def test_dns_rebinding_host_is_rejected(app: App) -> None:
    client = Client(app)
    assert client.request("GET", "/", host="evil.example:80")[0] == 403
    assert client.request("GET", "/api/state", host=f"attacker.com:{app.port}")[0] == 403
    assert client.request("GET", "/api/state", host=f"localhost:{app.port}")[0] == 200


def test_api_requires_session(app: App) -> None:
    client = Client(app)
    assert client.request("GET", "/api/state", cookie=False)[0] == 401
    assert client.request("POST", "/api/plan", {"task": "x"}, cookie=False)[0] == 401


def test_mutations_require_origin_csrf_and_json(app: App) -> None:
    client = Client(app)
    body = {"task": "fix typo in README.md"}
    assert client.request("POST", "/api/plan", body, origin=None)[0] == 403
    assert client.request("POST", "/api/plan", body, origin="http://evil.example")[0] == 403
    assert client.request("POST", "/api/plan", body, origin="null")[0] == 403
    assert client.request("POST", "/api/plan", body, csrf=False)[0] == 403
    assert client.request("POST", "/api/plan", body, content_type="text/plain")[0] == 415
    assert client.request("POST", "/api/plan", body)[0] == 200
    assert client.request("DELETE", "/api/runs/x")[0] == 405
    assert client.request("OPTIONS", "/api/plan")[0] == 405


def test_bad_bodies(app: App) -> None:
    client = Client(app)
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    connection.request(
        "POST",
        "/api/plan",
        body=b"not json",
        headers={
            "Host": f"127.0.0.1:{app.port}",
            "Cookie": f"timonel_session_{app.port}={app.session}",
            "Origin": f"http://127.0.0.1:{app.port}",
            "X-CSRF-Token": app.csrf,
            "Content-Type": "application/json",
        },
    )
    assert connection.getresponse().status == 400
    assert client.request("POST", "/api/plan", [1, 2])[0] == 400
    assert client.request("POST", "/api/plan", {"task": " "})[0] == 400
    assert client.request("POST", "/api/runs", {"task": ""})[0] == 400
    assert client.request("GET", "/api/nope")[0] == 404
    assert client.request("GET", "/nope")[0] == 404
    assert client.request("GET", "/api/runs/missing")[0] == 409


def test_run_flow_with_edited_plan_and_sse(app: App, repo: Path) -> None:
    client = Client(app)
    status, plan = client.json("POST", "/api/plan", {"task": "[[edit all:ui.txt=ok]] add ui file"})
    assert status == 200
    plan["ladders"] = [
        [{"tier": 2, "agent": "codex", "model": "o3", "effort": "low", "billing": "api"}]
    ]  # type: ignore[index]
    status, started = client.json(
        "POST", "/api/runs", {"task": "[[edit all:ui.txt=ok]] add ui file", "plan": plan}
    )
    assert status == 202
    run_id = started["run_id"]  # type: ignore[index]
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    connection.request(
        "GET",
        f"/api/runs/{run_id}/events",
        headers={
            "Host": f"127.0.0.1:{app.port}",
            "Cookie": f"timonel_session_{app.port}={app.session}",
        },
    )
    response = connection.getresponse()
    assert response.getheader("Content-Type", "").startswith("text/event-stream")
    stream = response.read().decode()
    assert "event: attempt.started" in stream
    assert "event: run.finished" in stream
    assert stream.rstrip().endswith("data: {}")
    status, detail = client.json("GET", f"/api/runs/{run_id}")
    assert detail["status"] == "succeeded"  # type: ignore[index]
    assert detail["attempts"][-1]["agent"] == "codex"  # type: ignore[index]
    assert detail["attempts"][-1]["model"] == "o3"  # type: ignore[index]
    assert detail["attempts"][-1]["billing"] == "subscription"  # type: ignore[index]
    status, diff = client.json("GET", f"/api/runs/{run_id}/diff")
    assert "+ok" in diff["diff"]  # type: ignore[index]
    status, listing = client.json("GET", "/api/runs?q=ui&status=succeeded")
    assert [run["id"] for run in listing] == [run_id]  # type: ignore[union-attr]
    status, applied = client.json("POST", f"/api/runs/{run_id}/apply")
    assert status == 200 and applied["status"] == "applied"  # type: ignore[index]
    assert (repo / "ui.txt").read_text() == "ok"
    status, _ = client.json("POST", f"/api/runs/{run_id}/discard")
    assert status == 409
    status, _error = client.json("POST", f"/api/runs/{run_id}/cancel")
    assert status == 409
    status, stats = client.json("GET", "/api/stats")
    assert stats["runs"] == 1  # type: ignore[index]
    status, state = client.json("GET", "/api/state")
    assert state["recent"][0]["id"] == run_id  # type: ignore[index]
    status, _ = client.json("POST", "/api/doctor")
    assert status == 200


def test_edited_plan_is_validated(app: App) -> None:
    client = Client(app)
    good_class = {
        "complexity": 1,
        "kind": "edit",
        "risk": "low",
        "needs_write": True,
        "estimated_files": 1,
    }
    cases = [
        "nope",
        {"classification": {"complexity": 9}, "ladders": [[]]},
        {"classification": good_class, "ladders": []},
        {"classification": good_class, "ladders": [[]]},
        {"classification": good_class, "ladders": [["x"]]},
        {"classification": good_class, "ladders": [[{"tier": 1, "agent": "rm -rf"}]]},
        {"classification": good_class, "ladders": [[{"tier": 9, "agent": "codex"}]]},
        {
            "classification": good_class,
            "ladders": [[{"tier": 1, "agent": "codex", "model": "x" * 300}]],
        },
    ]
    for plan in cases:
        assert client.request("POST", "/api/runs", {"task": "x", "plan": plan})[0] == 400


def test_cancel_running_task(app: App, fakes: Fakes) -> None:
    app.engine.environ["FAKE_MODE"] = "silent_hang"
    client = Client(app)
    good_class = {
        "complexity": 1,
        "kind": "edit",
        "risk": "low",
        "needs_write": True,
        "estimated_files": 1,
    }
    plan = {
        "classification": good_class,
        "ladders": [[{"tier": 1, "agent": "copilot", "model": "auto"}]],
    }
    _status, started = client.json("POST", "/api/runs", {"task": "hang", "plan": plan})
    run_id = started["run_id"]  # type: ignore[index]
    time.sleep(1.5)
    assert client.json("POST", f"/api/runs/{run_id}/cancel")[0] == 200
    app.threads[run_id].join(20)
    assert not app.threads[run_id].is_alive()
    assert client.json("GET", f"/api/runs/{run_id}")[1]["status"] == "cancelled"  # type: ignore[index]
    assert client.json("POST", f"/api/runs/{run_id}/discard")[1]["status"] == "discarded"  # type: ignore[index]


def test_routes_editor_validates_and_writes(app: App, home: Path) -> None:
    client = Client(app)
    status, routes = client.json("GET", "/api/routes")
    assert set(routes["tiers"]) == {"1", "2", "3", "4"}  # type: ignore[index]
    bad = {"tiers": {"1": [{"agent": "ghost"}]}}
    status, error = client.json("PUT", "/api/routes", bad)
    assert status == 422
    assert any("unknown agent 'ghost'" in item for item in error["details"])  # type: ignore[index]
    assert client.request("PUT", "/api/routes", {"tiers": "x"})[0] == 400
    good = {
        "tiers": {
            "1": [{"agent": "codex", "model": "", "effort": "low"}],
            "2": [],
            "3": [{"agent": "claude", "model": "opus", "effort": "high"}],
        }
    }
    status, saved = client.json("PUT", "/api/routes", good)
    assert status == 200
    written = yaml.safe_load(Path(saved["saved"]).read_text(encoding="utf-8"))  # type: ignore[index]
    assert written["tiers"] == {1: good["tiers"]["1"], 3: good["tiers"]["3"]}
    assert app.engine.config.tiers[1][0].agent == "codex"
    status, saved = client.json("PUT", "/api/routes", good)
    assert (
        Path(saved["saved"] + ".bak").exists()
        or Path(saved["saved"]).with_suffix(".yaml.bak").exists()
    )  # type: ignore[index]


def test_secrets_never_reach_the_ui(app: App, fakes: Fakes) -> None:
    client = Client(app)
    secret = fakes.env["DEEPSEEK_API_KEY"]
    good_class = {
        "complexity": 1,
        "kind": "explain",
        "risk": "low",
        "needs_write": False,
        "estimated_files": 0,
    }
    plan = {
        "classification": good_class,
        "ladders": [[{"tier": 1, "agent": "deepseek", "model": "deepseek-flash"}]],
    }
    _status, started = client.json(
        "POST", "/api/runs", {"task": f"[[say:{secret}]] explain", "plan": plan}
    )
    run_id = started["run_id"]  # type: ignore[index]
    app.threads[run_id].join(20)
    for path in (f"/api/runs/{run_id}", f"/api/runs/{run_id}/events", "/api/state", "/api/runs"):
        assert secret.encode() not in client.request("GET", path)[2]


def test_negative_content_length_and_event_resume(app: App) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    connection.putrequest("POST", "/api/plan", skip_host=True)
    for name, value in (
        ("Host", f"127.0.0.1:{app.port}"),
        ("Cookie", f"timonel_session_{app.port}={app.session}"),
        ("Origin", f"http://127.0.0.1:{app.port}"),
        ("X-CSRF-Token", app.csrf),
        ("Content-Type", "application/json"),
        ("Content-Length", "-5"),
    ):
        connection.putheader(name, value)
    connection.endheaders()
    assert connection.getresponse().status == 400
    client = Client(app)
    plan = {
        "classification": {
            "complexity": 1,
            "kind": "explain",
            "risk": "low",
            "needs_write": False,
            "estimated_files": 0,
        },
        "ladders": [[{"tier": 1, "agent": "codex"}]],
    }
    _status, started = client.json(
        "POST", "/api/runs", {"task": "[[say:hi]] explain", "plan": plan}
    )
    run_id = started["run_id"]  # type: ignore[index]
    app.threads[run_id].join(30)
    events = app.engine.ledger.events(run_id)
    last = events[-2]["id"]
    stream = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    stream.request(
        "GET",
        f"/api/runs/{run_id}/events",
        headers={
            "Host": f"127.0.0.1:{app.port}",
            "Cookie": f"timonel_session_{app.port}={app.session}",
            "Last-Event-ID": str(last),
        },
    )
    body = stream.getresponse().read().decode()
    assert body.count("id: ") == 1
    assert "event: run.finished" in body


def test_config_endpoints_validate_preview_and_save(app: App, home: Path) -> None:
    client = Client(app)
    status, view = client.json("GET", "/api/config")
    assert status == 200
    options = {item["key"]: item for item in view["options"]}  # type: ignore[index]
    assert options["ui.density"]["value"] == "comfortable"
    assert options["ui.density"]["origin"] == "default"
    assert set(options["ui.density"]["description"]) == {"en", "es", "pt"}
    status, checked = client.json(
        "POST", "/api/config/validate", {"values": {"verification": {"timeout_s": -5}}}
    )
    assert status == 200
    assert any("verification.timeout_s" in item for item in checked["problems"])  # type: ignore[index]
    status, error = client.json("PUT", "/api/config", {"values": {"ui": {"density": "huge"}}})
    assert status == 422
    assert client.request("POST", "/api/config/validate", {"nothing": 1})[0] == 400
    status, saved = client.json("PUT", "/api/config", {"values": {"ui": {"density": "compact"}}})
    assert status == 200
    assert "density: compact" in Path(saved["saved"]).read_text(encoding="utf-8")  # type: ignore[index]
    assert app.engine.config.ui.density == "compact"
    options = {item["key"]: item for item in saved["options"]}  # type: ignore[index]
    assert options["ui.density"]["origin"] == "user"
    status, saved = client.json("PUT", "/api/config", {"yaml": "ui:\n  theme: dark\n"})
    assert status == 200
    assert app.engine.config.ui.theme == "dark"
    assert Path(saved["saved"] + ".bak").exists()  # type: ignore[index]


def test_setup_endpoint_and_install_confirmation(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    from timonel import wizard

    client = Client(app)
    status, data = client.json("GET", "/api/setup")
    assert status == 200
    items = {item["key"]: item for item in data["items"]}  # type: ignore[index]
    assert items["claude"]["installed"] is True
    assert items["deepseek"]["key_env"] == "DEEPSEEK_API_KEY"
    assert client.json("POST", "/api/setup/install", {"key": "ghost"})[0] == 404
    ran: list[list[str]] = []
    monkeypatch.setattr(wizard, "detect_managers", lambda environ: ["npm"])
    monkeypatch.setattr(wizard, "reachable", lambda host, environ: True)
    monkeypatch.setattr(wizard, "refresh_windows_path", lambda environ: False)
    import timonel.web.server as server

    def fake_runner(lines: list[str]) -> object:
        def run(argv: list[str]) -> int:
            ran.append(argv)
            lines.append("added 1 package")
            return 0

        return run

    monkeypatch.setattr(server, "captured_runner", fake_runner)
    status, preview = client.json("POST", "/api/setup/install", {"key": "gemini"})
    assert preview["status"] == "confirm"  # type: ignore[index]
    assert preview["command"].startswith("npm install -g")  # type: ignore[index]
    assert ran == []
    status, result = client.json("POST", "/api/setup/install", {"key": "gemini", "confirm": True})
    assert ran and ran[0][:3] == ["npm", "install", "-g"]
    assert "added 1 package" in result["lines"]  # type: ignore[index]
    cursor = next(item for item in wizard.agent_components(app.config) if item.key == "cursor")
    monkeypatch.setattr(wizard, "detect_managers", lambda environ: [])
    status, terminal = client.json(
        "POST", "/api/setup/install", {"key": cursor.key, "confirm": True}
    )
    assert terminal["status"] == "terminal"  # type: ignore[index]
    assert len(ran) == 1
