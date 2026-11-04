from __future__ import annotations

import getpass
import hashlib
import importlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml

from timonel import keys
from timonel.adapters import strip_controls
from timonel.config import Config
from timonel.doctor import check_auth, safe_env
from timonel.i18n import t
from timonel.ledger import data_dir
from timonel.proc import LaunchError, resolve_launch, which

OsName = Literal["windows", "macos", "linux"]
MANAGERS = ("winget", "scoop", "choco", "brew", "apt", "dnf", "pacman", "npm", "pnpm", "pipx", "uv")
SYSTEM_PREFERENCE: dict[str, tuple[str, ...]] = {
    "windows": ("winget", "scoop", "choco"),
    "macos": ("brew",),
    "linux": ("apt", "dnf", "pacman", "brew"),
}
AGENT_PREFERENCE = ("npm", "pnpm", "pipx", "uv")
MANAGER_COMMAND = {"apt": "apt-get"}
NETWORK_HOSTS = {
    "npm": "registry.npmjs.org",
    "pnpm": "registry.npmjs.org",
    "pipx": "pypi.org",
    "uv": "pypi.org",
}
SCRIPT_PREVIEW_LINES = 40

Ask = Callable[[str], str]
Say = Callable[[str], None]
Runner = Callable[[list[str]], int]


class NoPackageManager(Exception):
    pass


class NetworkUnavailable(Exception):
    pass


class InstallFailed(Exception):
    pass


@dataclass(frozen=True)
class InstallSpec:
    manager: str
    argv: tuple[str, ...] = ()
    manual: str = ""
    script: str = ""
    verified: str = ""

    @property
    def elevated(self) -> bool:
        return bool(self.argv) and self.argv[0] != MANAGER_COMMAND.get(self.manager, self.manager)


@dataclass(frozen=True)
class Component:
    key: str
    kind: Literal["tool", "agent", "provider"]
    title: str
    command: str
    install: tuple[InstallSpec, ...] = ()
    uninstall: dict[str, tuple[str, ...]] = field(default_factory=dict)
    size_mb: int | None = None
    optional: bool = False
    login: tuple[str, ...] = ()
    flow: str = "browser"
    key_env: str = ""
    key_url: str = ""
    what: str = ""
    homepage: str = ""


@dataclass
class Plan:
    os: OsName
    managers: list[str]
    components: list[Component]


@dataclass
class Session:
    ask: Ask
    say: Say
    runner: Runner
    environ: dict[str, str]
    dry_run: bool = False
    assume_yes: bool = False
    allow: frozenset[str] = frozenset()
    state_path: Path = field(default_factory=lambda: data_dir() / "start-state.json")
    audit_path: Path = field(default_factory=lambda: data_dir() / "audit.log")
    fetch: Callable[[str], bytes] = field(default_factory=lambda: download)
    results: dict[str, str] = field(default_factory=dict)


def detect_os(system: str | None = None) -> OsName:
    name = (system or platform.system()).lower()
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    return "linux"


def detect_managers(environ: Mapping[str, str]) -> list[str]:
    path = environ.get("PATH")
    return [name for name in MANAGERS if which(MANAGER_COMMAND.get(name, name), path)]


@cache
def tool_data() -> dict[str, Any]:
    text = resources.files("timonel").joinpath("components.yaml").read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def specs_from(install: Mapping[str, Any]) -> tuple[InstallSpec, ...]:
    found: list[InstallSpec] = []
    for manager, value in install.items():
        if manager in ("approx_size_mb", "uninstall", "drift"):
            continue
        if manager == "script" and isinstance(value, dict):
            found.append(InstallSpec("script", script=json.dumps(value)))
        elif isinstance(value, list):
            found.append(InstallSpec(manager, tuple(str(part) for part in value)))
        elif isinstance(value, dict):
            found.append(
                InstallSpec(
                    manager,
                    tuple(str(part) for part in value.get("argv", [])),
                    str(value.get("manual", "")),
                    "",
                    str(value.get("verified", "")),
                )
            )
    return tuple(found)


def tool_components() -> list[Component]:
    found = []
    for key, data in tool_data().items():
        login = data.get("login") or {}
        found.append(
            Component(
                key=key,
                kind="tool",
                title=data["title"],
                command=data["command"],
                install=specs_from(data.get("install", {})),
                uninstall={
                    name: tuple(argv) for name, argv in (data.get("uninstall") or {}).items()
                },
                optional=bool(data.get("optional")),
                login=tuple(login.get("argv", [])),
                flow=str(login.get("flow", "browser")),
                what=t(f"tool.{key}.what"),
                homepage=data.get("homepage", ""),
            )
        )
    return found


def agent_components(config: Config) -> list[Component]:
    found = []
    for name, agent in config.agents.items():
        meta = agent.meta
        install = meta.get("install") or {}
        login = meta.get("login") or {}
        api_key = meta.get("api_key") or {}
        status = (meta.get("verification") or {}).get("status", "unverified")
        found.append(
            Component(
                key=name,
                kind="provider" if agent.kind == "provider" else "agent",
                title=str(meta.get("title", name)),
                command=agent.command,
                install=specs_from(install),
                uninstall={
                    key: tuple(value) for key, value in (install.get("uninstall") or {}).items()
                },
                size_mb=install.get("approx_size_mb"),
                login=tuple(login.get("command", [])),
                flow=str(login.get("flow", "browser")),
                key_env=str(api_key.get("env", "")),
                key_url=str(api_key.get("url", meta.get("homepage", ""))),
                what=t(
                    "provider.what" if agent.kind == "provider" else "agent.what",
                    title=meta.get("title", name),
                    vendor=meta.get("vendor", "?"),
                    status=t(f"agent.status.{status}"),
                ),
                homepage=str(meta.get("homepage", "")),
            )
        )
    return found


def pick_install(
    component: Component, os_name: OsName, managers: Sequence[str]
) -> InstallSpec | None:
    order = (
        SYSTEM_PREFERENCE[os_name]
        if component.kind == "tool"
        else (*AGENT_PREFERENCE, *SYSTEM_PREFERENCE[os_name])
    )
    by_manager = {spec.manager: spec for spec in component.install}
    for manager in order:
        if manager in by_manager and manager in managers:
            return by_manager[manager]
    for manager in order:
        spec = by_manager.get(manager)
        if spec is not None and spec.manual:
            return spec
    return by_manager.get("script")


def installed(component: Component, environ: Mapping[str, str]) -> bool:
    if component.kind == "provider":
        return True
    return which(component.command, environ.get("PATH")) is not None


def auth_state(component: Component, config: Config, environ: Mapping[str, str]) -> str:
    agent = config.agents.get(component.key)
    if agent is None:
        return "unknown"
    if component.key_env and (environ.get(component.key_env) or keys.present(component.key_env)):
        return "ok"
    state, _ = check_auth(agent, safe_env(agent, config, environ), environ)
    return state


def read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"done": []}
    return data if isinstance(data, dict) else {"done": []}


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def audit(
    session: Session,
    action: str,
    component: str,
    command: Sequence[str],
    code: int | None,
    undo: Sequence[str] = (),
) -> None:
    session.audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": action,
        "component": component,
        "command": list(command),
        "exit_code": code,
        "dry_run": session.dry_run,
        "undo": list(undo),
    }
    with session.audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def run_inherited(argv: list[str]) -> int:
    try:
        launch = resolve_launch(argv)
    except LaunchError:
        return 127
    return subprocess.run(launch, check=False).returncode


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        data: bytes = response.read()
    return data


def refresh_windows_path(environ: dict[str, str]) -> bool:
    if os.name != "nt":
        return False
    winreg: Any = importlib.import_module("winreg")

    paths = []
    for root, key in (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        paths.extend(os.path.expandvars(str(value)).split(os.pathsep))
    merged = [
        entry
        for entry in dict.fromkeys([*environ.get("PATH", "").split(os.pathsep), *paths])
        if entry
    ]
    environ["PATH"] = os.pathsep.join(merged)
    os.environ["PATH"] = environ["PATH"]
    return True


def reachable(host: str, environ: Mapping[str, str]) -> bool:
    if any(environ.get(name) for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY")):
        return True
    try:
        with socket.create_connection((host, 443), timeout=5):
            return True
    except OSError:
        return False


def yes(answer: str) -> bool:
    return answer.strip().lower() in t("common.yes_words").split(",")


def build_plan(
    config: Config, wanted: Sequence[str], environ: Mapping[str, str], system: str | None = None
) -> Plan:
    os_name = detect_os(system)
    managers = detect_managers(environ)
    tools = [
        component
        for component in tool_components()
        if component.key in ("git", "node", "gh", "pipx")
    ]
    agents = [component for component in agent_components(config) if component.key in wanted]
    needs_node = any(
        spec.manager in ("npm", "pnpm") for component in agents for spec in component.install
    )
    needs_pipx = any(
        spec.manager == "pipx" for component in agents for spec in component.install
    ) and not any(
        spec.manager in ("npm", "pnpm") for component in agents for spec in component.install
    )
    chosen = [
        tool
        for tool in tools
        if tool.key == "git"
        or (tool.key == "node" and needs_node)
        or (tool.key == "pipx" and needs_pipx)
        or (tool.key == "gh" and ("gh" in wanted or "copilot" in wanted))
    ]
    return Plan(os_name, managers, [*chosen, *agents])


def explain_component(session: Session, component: Component, spec: InstallSpec | None) -> None:
    session.say(f"\n== {component.title} ==")
    session.say(f"{t('wizard.what')}: {component.what}")
    if component.homepage:
        session.say(f"{t('common.docs')}: {component.homepage}")
    if spec is not None and spec.argv:
        session.say(f"{t('wizard.command')}: {' '.join(spec.argv)}")
        session.say(
            t("wizard.size", size=f"~{component.size_mb} MB")
            if component.size_mb
            else t("wizard.size_unknown")
        )
        if component.kind == "tool" and not spec.verified:
            session.say(t("wizard.unverified_command"))


def decide(session: Session, component: Component, prompt_key: str) -> str:
    if session.assume_yes:
        return "i" if component.key in session.allow else "s"
    while True:
        answer = session.ask(t(prompt_key) + " ").strip().lower()[:1]
        if answer in ("i", "l", "k", "s", "q"):
            return answer
        if answer == "e":
            session.say(t("learn.agent.body") if component.kind != "tool" else component.what)


def install_component(session: Session, plan: Plan, component: Component) -> str:
    plan.managers = detect_managers(session.environ)
    spec = pick_install(component, plan.os, plan.managers)
    explain_component(session, component, spec)
    if session.assume_yes and component.key not in session.allow:
        session.say(t("wizard.not_allowed", name=component.title))
        return "skipped"
    if spec is None:
        session.say(t("wizard.no_manager", name=component.title))
        session.say(t("error.TMN-8002.fix"))
        return "no_manager"
    choice = decide(session, component, "wizard.choice")
    if choice == "q":
        raise KeyboardInterrupt
    if choice == "s":
        return "skipped"
    if spec.manual:
        session.say(t("wizard.manual", url=spec.manual))
        return "manual"
    if session.dry_run:
        audit(session, "install", component.key, spec.argv or [spec.script], None)
        return "dry_run"
    if spec.manager in NETWORK_HOSTS and not reachable(
        NETWORK_HOSTS[spec.manager], session.environ
    ):
        session.say(t("wizard.offline", host=NETWORK_HOSTS[spec.manager]))
        return "offline"
    if spec.manager == "script":
        return run_script(session, component, spec)
    if spec.elevated:
        session.say(t("wizard.elevation", manager=spec.manager))
        session.say(f"  {' '.join(spec.argv)}")
        if not session.assume_yes:
            session.ask("")
        audit(
            session,
            "install-manual",
            component.key,
            spec.argv,
            None,
            component.uninstall.get(spec.manager, ()),
        )
    else:
        session.say(t("wizard.running", command=" ".join(spec.argv)))
        code = session.runner(list(spec.argv))
        audit(
            session,
            "install",
            component.key,
            spec.argv,
            code,
            component.uninstall.get(spec.manager, ()),
        )
        if code != 0:
            session.say(t("error.TMN-8003.title") + f" (exit {code})")
            session.say(t("error.TMN-8003.fix"))
            return "failed"
    if refresh_windows_path(session.environ):
        session.say(t("wizard.path_refreshed"))
    if installed(component, session.environ):
        session.say(t("wizard.installed", name=component.title))
        if component.uninstall.get(spec.manager):
            session.say(t("wizard.undo", command=" ".join(component.uninstall[spec.manager])))
        return "installed"
    session.say(t("wizard.not_found_after", name=component.title))
    return "not_found"


def run_script(session: Session, component: Component, spec: InstallSpec) -> str:
    urls = json.loads(spec.script)
    url = urls.get("windows" if detect_os() == "windows" else "posix")
    if not url:
        return "no_manager"
    body = session.fetch(url)
    digest = hashlib.sha256(body).hexdigest()
    folder = Path(tempfile.mkdtemp(prefix="timonel-installer-"))
    script = folder / ("install.ps1" if detect_os() == "windows" else "install.sh")
    script.write_bytes(body)
    session.say(t("wizard.script_intro"))
    session.say(f"  {url} -> {script}")
    lines = strip_controls(body.decode("utf-8", errors="replace")).splitlines()
    for line in lines[:SCRIPT_PREVIEW_LINES]:
        session.say(f"    {line}")
    if len(lines) > SCRIPT_PREVIEW_LINES:
        session.say(t("wizard.script_more", count=len(lines) - SCRIPT_PREVIEW_LINES, path=script))
    session.say(t("wizard.script_sha", sha=digest))
    try:
        if session.assume_yes or not yes(session.ask(t("wizard.script_confirm") + " [y/N] ")):
            audit(session, "script-declined", component.key, [url], None)
            return "skipped"
        argv = (
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
            if script.suffix == ".ps1"
            else ["sh", str(script)]
        )
        code = session.runner(argv)
        audit(session, "install-script", component.key, [url, f"sha256={digest}"], code)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return "installed" if code == 0 and installed(component, session.environ) else "failed"


def login_component(session: Session, config: Config, component: Component) -> str:
    if component.login and auth_state(component, config, session.environ) != "ok":
        session.say(t("wizard.login_intro", name=component.title))
        session.say(t(f"wizard.flow.{component.flow}"))
        if session.assume_yes:
            return "skipped"
        choice = decide(session, component, "wizard.choice_login")
        if choice == "q":
            raise KeyboardInterrupt
        if choice != "l" or session.dry_run:
            return "skipped"
        code = session.runner(list(component.login))
        audit(session, "login", component.key, component.login, code)
        state = auth_state(component, config, session.environ)
        session.say(
            t("wizard.login_ok" if state == "ok" else "wizard.login_unknown", name=component.title)
        )
        return "logged_in" if state == "ok" else "login_unknown"
    if component.key_env and auth_state(component, config, session.environ) != "ok":
        return store_key(session, component)
    return "ok"


def store_key(
    session: Session, component: Component, secret_input: Callable[[str], str] = getpass.getpass
) -> str:
    session.say(
        t("wizard.key_intro", name=component.title, env=component.key_env, url=component.key_url)
    )
    if session.assume_yes:
        return "skipped"
    choice = decide(session, component, "wizard.choice_key")
    if choice == "q":
        raise KeyboardInterrupt
    if choice != "k" or session.dry_run:
        return "skipped"
    value = secret_input(t("wizard.key_prompt")).strip()
    if not value:
        session.say(t("wizard.key_empty"))
        return "skipped"
    try:
        keys.store(component.key_env, value)
    except keys.KeyringUnavailable:
        session.say(t("error.TMN-8010.title"))
        session.say(t("error.TMN-8010.fix"))
        return "no_keyring"
    audit(
        session,
        "store-key",
        component.key,
        [component.key_env],
        0,
        ["timonel", "keys", "delete", component.key_env],
    )
    session.say(t("wizard.key_saved", env=component.key_env))
    return "key_stored"


def run_wizard(
    config: Config, session: Session, wanted: Sequence[str], system: str | None = None
) -> dict[str, str]:
    session.say(t("wizard.welcome"))
    if session.dry_run:
        session.say(t("common.dry_run"))
    plan = build_plan(config, wanted, session.environ, system)
    session.say(
        t("wizard.system", os=plan.os, managers=", ".join(plan.managers) or t("wizard.none"))
    )
    for variable in ("HTTPS_PROXY", "https_proxy"):
        if session.environ.get(variable):
            session.say(t("wizard.proxy", var=variable))
    state = read_state(session.state_path)
    done = set(state.get("done", []))
    if done:
        session.say(t("wizard.resume", count=len(done)))
    for component in plan.components:
        step = f"install:{component.key}"
        if installed(component, session.environ):
            session.results[component.key] = "ok"
        elif step in done:
            session.results[component.key] = "done_before"
            continue
        else:
            session.results[component.key] = install_component(session, plan, component)
            if session.results[component.key] == "installed" and not session.dry_run:
                done.add(step)
                write_state(session.state_path, {"done": sorted(done)})
        if (component.kind != "tool" or component.login) and installed(component, session.environ):
            outcome = login_component(session, config, component)
            if outcome != "ok":
                session.results[f"{component.key}:auth"] = outcome
    if "copilot" in wanted:
        session.say(t("wizard.student"))
    session.say(t("wizard.terms"))
    session.say(t("wizard.audit", path=session.audit_path))
    session.say(t("wizard.next"))
    return session.results


def interactive_session(
    environ: dict[str, str], dry_run: bool, assume_yes: bool, allow: Sequence[str]
) -> Session:
    def ask(prompt: str) -> str:
        if not sys.stdin.isatty():
            return "s"
        return input(prompt)

    return Session(
        ask=ask,
        say=print,
        runner=run_inherited,
        environ=environ,
        dry_run=dry_run,
        assume_yes=assume_yes,
        allow=frozenset(allow),
    )
