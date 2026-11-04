from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from rich.console import Console, Group, RenderableType
from rich.table import Table
from rich.text import Text

from timonel import __version__, gitops
from timonel.config import Config, user_config_path
from timonel.doctor import AgentHealth, default_names, inspect_agent
from timonel.gitops import GitError
from timonel.i18n import t
from timonel.ledger import Ledger, data_dir

State = Literal[
    "checking", "unchecked", "ready", "login_unknown", "login", "unavailable", "no_answer"
]
LogoStyle = Literal["color", "mono", "ascii", "none"]

LOGO = (
    "        ██",
    "  ▄█▄ ▄▄██▄▄ ▄█▄",
    "   ▀██▀▀██▀▀██▀",
    "   ██ ▀▄██▄▀ ██",
    "██████████████████",
    "   ██ ▄▀██▀▄ ██",
    "   ▄██▄▄██▄▄██▄",
    "  ▀█▀ ▀▀██▀▀ ▀█▀",
    "        ██",
)
LOGO_ASCII = (
    "     \\  |  /",
    "   .--\\-|-/--.",
    "  /    \\|/    \\",
    "--|----(O)----|--",
    "  \\    /|\\    /",
    "   '--/-|-\\--'",
    "     /  |  \\",
)
LOGO_STYLES: dict[str, LogoStyle] = {
    "color": "color",
    "mono": "mono",
    "ascii": "ascii",
    "none": "none",
}
BRASS = {"dark": "#d9a441", "light": "#8a5a12", "auto": "#c08a2c"}
STATE_STYLE: dict[str, str] = {
    "ready": "green",
    "login_unknown": "yellow",
    "login": "red",
    "unavailable": "bright_black",
    "checking": "cyan",
    "unchecked": "bright_black",
    "no_answer": "yellow",
}
VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")
CACHE_FILE = "agent-status.json"


@dataclass(frozen=True)
class AgentStatus:
    name: str
    state: State
    version: str = ""
    detail: str = ""
    checked_at: float = 0.0


@dataclass(frozen=True)
class Place:
    cwd: Path
    repo: Path | None = None
    branch: str = ""
    dirty: bool = False


@dataclass(frozen=True)
class Notice:
    text: str
    command: str


@dataclass(frozen=True)
class HomeInfo:
    version: str
    profile: str
    mode: str
    place: Place
    agents: tuple[str, ...]
    coverage: dict[str, tuple[str, ...]]
    spend_today: float | None = None
    budget: str = ""
    notices: tuple[Notice, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Glyphs:
    dot: str
    rule: str
    arrow: str
    warn: str
    helm: str
    sep: str
    prompt: str
    more: str


UNICODE = Glyphs("●", "─", "→", "!", "⎈", "·", "\u203a", "…")
ASCII = Glyphs("*", "-", "->", "!", "(*)", "|", ">", "...")


def unicode_ok(stream: Any = None) -> bool:
    target = stream if stream is not None else sys.stdout
    encoding = (getattr(target, "encoding", None) or "utf-8").lower().replace("-", "")
    return encoding.startswith("utf") and os.environ.get("TERM") != "dumb"


def pick_logo(setting: str, console: Console) -> LogoStyle:
    chosen = LOGO_STYLES.get(setting)
    if chosen is not None:
        return chosen
    if not unicode_ok(console.file):
        return "ascii"
    return "mono" if console.no_color or console.color_system is None else "color"


def place_of(cwd: Path) -> Place:
    try:
        repo = gitops.repo_root(cwd)
    except GitError:
        return Place(cwd=cwd)
    try:
        branch = gitops.git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
        dirty = bool(gitops.git(repo, "status", "--porcelain", "--untracked-files=no").strip())
    except GitError:
        return Place(cwd=cwd, repo=repo)
    return Place(cwd=cwd, repo=repo, branch=branch or "HEAD", dirty=dirty)


def tier_span(tiers: list[int]) -> str:
    groups: list[list[int]] = []
    for tier in sorted(set(tiers)):
        if groups and tier == groups[-1][-1] + 1:
            groups[-1].append(tier)
        else:
            groups.append([tier])
    return ",".join(
        f"T{group[0]}" if len(group) == 1 else f"T{group[0]}-T{group[-1]}" for group in groups
    )


def coverage(config: Config) -> dict[str, tuple[str, ...]]:
    models: dict[str, dict[str, list[int]]] = {}
    for tier, rungs in sorted(config.tiers.items()):
        for rung in rungs:
            label = rung.model or t("home.default_model")
            models.setdefault(rung.agent, {}).setdefault(label, []).append(tier)
    found: dict[str, list[str]] = {
        name: [f"{tier_span(tiers)} {model}" for model, tiers in by_model.items()]
        for name, by_model in models.items()
    }
    for role, ladder in (
        ("home.role.classify", config.classifier.ladder),
        ("home.role.plan", config.planner.ladder),
        ("home.role.review", config.review.reviewers),
    ):
        for rung in ladder:
            label = t(role)
            if label not in found.setdefault(rung.agent, []):
                found[rung.agent].append(label)
    return {name: tuple(items) for name, items in found.items()}


def version_tuple(text: str) -> tuple[int, ...] | None:
    match = VERSION_RE.search(text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def outdated(config: Config, status: AgentStatus) -> str | None:
    agent = config.agents.get(status.name)
    if agent is None or status.state not in ("ready", "login", "login_unknown"):
        return None
    verification = agent.meta.get("verification") or {}
    checked = version_tuple(str(verification.get("version") or ""))
    current = version_tuple(status.version)
    if checked is None or current is None or current >= checked:
        return None
    return ".".join(map(str, checked))


def install_command(config: Config, name: str) -> str:
    install = (config.agents[name].meta.get("install") or {}) if name in config.agents else {}
    for manager in ("npm", "pnpm", "pipx", "uv", "brew", "winget"):
        argv = install.get(manager)
        if isinstance(argv, list) and argv:
            return " ".join(map(str, argv))
    return "timonel start"


def login_command(config: Config, name: str) -> str:
    agent = config.agents.get(name)
    login = (agent.meta.get("login") or {}) if agent else {}
    argv = login.get("command") or []
    if argv:
        return " ".join(map(str, argv))
    key_env = ((agent.meta.get("api_key") or {}).get("env") if agent else None) or ""
    return f"timonel keys set {key_env}" if key_env else f"timonel agents info {name}"


def notices(
    config: Config, place: Place, statuses: Mapping[str, AgentStatus]
) -> tuple[Notice, ...]:
    wanted = set(config.tui.notices)
    found: list[Notice] = []
    if "first_run" in wanted and not user_config_path().is_file():
        found.append(Notice(t("home.notice.first_run"), "timonel start"))
    if "untrusted" in wanted and config.project.path is not None and not config.project.trusted:
        found.append(Notice(t("home.notice.untrusted"), "timonel trust"))
    if "not_git" in wanted and place.repo is None:
        found.append(Notice(t("home.notice.not_git"), "/git-init"))
    for status in statuses.values():
        if "login" in wanted and status.state == "login":
            found.append(
                Notice(
                    t("home.notice.login", agent=status.name), login_command(config, status.name)
                )
            )
        if "outdated" in wanted and (checked := outdated(config, status)):
            found.append(
                Notice(
                    t("home.notice.outdated", agent=status.name, version=checked),
                    install_command(config, status.name),
                )
            )
    return tuple(found)


def budget_line(config: Config, ledger: Ledger) -> str:
    from timonel.engine import Engine

    engine = Engine(config, ledger)
    for budget in config.budgets:
        if budget.max_usd is None:
            continue
        cost, _, _ = engine.budget_usage(budget)
        left = max(budget.max_usd - cost, 0.0)
        return t(
            "home.budget",
            left=f"${left:.2f}",
            total=f"${budget.max_usd:.2f}",
            period=t(f"home.period.{budget.period}"),
        )
    return ""


def gather(
    config: Config,
    cwd: Path,
    statuses: Mapping[str, AgentStatus],
    profile: str | None = None,
    mode: str | None = None,
    ledger: Ledger | None = None,
) -> HomeInfo:
    from timonel.ledger import open_ledger
    from timonel.stats import spend_since, start_of_day

    place = place_of(cwd)
    spend: float | None = None
    budget = ""
    if config.tui.show_spend:
        book = ledger or open_ledger(config.paths.ledger)
        spend = spend_since(book, start_of_day())
        budget = budget_line(config, book)
    return HomeInfo(
        version=__version__,
        profile=profile or config.default_profile,
        mode=mode or config.mode,
        place=place,
        agents=tuple(sorted(default_names(config))),
        coverage=coverage(config),
        spend_today=spend,
        budget=budget,
        notices=notices(config, place, statuses),
    )


def short_path(path: Path) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home) and home != "/" else text


def elide(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    parts = text.replace("\\", "/").split("/")
    tail = "/".join(parts[-2:])
    return "…/" + tail if len(tail) + 2 <= limit else "…" + text[-(limit - 1) :]


def git_line(place: Place) -> str:
    if place.repo is None:
        return t("home.no_git")
    state = t("home.dirty") if place.dirty else t("home.clean")
    return f"{t('home.branch')} {place.branch}, {state}"


def place_line(place: Place, limit: int = 48) -> str:
    return f"{elide(short_path(place.cwd), limit)} ({git_line(place)})"


def state_text(status: AgentStatus) -> str:
    return t(f"home.state.{status.state}")


def logo_block(style: LogoStyle, theme: str) -> Text | None:
    if style == "none":
        return None
    lines = LOGO_ASCII if style == "ascii" else LOGO
    color = BRASS.get(theme, BRASS["auto"]) if style == "color" else ""
    return Text("\n".join(lines), style=color, no_wrap=True)


def info_lines(info: HomeInfo, glyphs: Glyphs, color: bool) -> Text:
    bold = "bold" if color else ""
    text = Text(no_wrap=False)
    text.append(f"Timonel {info.version}", style=bold)
    text.append(f"  {glyphs.sep}  {t('home.tagline')}\n", style="dim" if color else "")
    text.append(f"{t('home.profile')}: ")
    text.append(info.profile, style=bold)
    text.append(f"   {t('home.mode')}: ")
    text.append(t(f"home.mode.{info.mode}"), style=bold)
    text.append("\n")
    text.append(elide(short_path(info.place.cwd), 40) + "\n")
    text.append(git_line(info.place) + "\n", style="yellow" if color and info.place.dirty else "")
    if info.spend_today is not None:
        text.append(f"{t('home.spend_today')}: ${info.spend_today:.2f}\n")
        if info.budget:
            text.append(info.budget + "\n")
    return text


def fit(text: str, width: int, more: str = "…") -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(width - len(more), 0)] + more


def agents_table(
    info: HomeInfo,
    statuses: Mapping[str, AgentStatus],
    glyphs: Glyphs,
    color: bool,
    width: int = 120,
) -> Text:
    rows = [(name, statuses.get(name, AgentStatus(name, "unchecked"))) for name in info.agents]
    name_width = max((len(name) for name, _ in rows), default=4)
    state_width = max((len(state_text(status)) for _, status in rows), default=4)
    lead = len(glyphs.dot) + 1 + name_width + 1 + state_width + 1
    room = width - lead
    narrow = room < 24
    text = Text(no_wrap=True, overflow="ellipsis")
    for index, (name, status) in enumerate(rows):
        style = STATE_STYLE[status.state] if color else ""
        covers = f" {glyphs.sep} ".join(info.coverage.get(name, ()))
        if not unicode_ok() or glyphs is ASCII:
            covers = covers.encode("ascii", "replace").decode()
        if index:
            text.append("\n")
        text.append(glyphs.dot + " ", style=style)
        text.append(name.ljust(name_width) + " ", style="bold" if color else "")
        text.append(state_text(status).ljust(state_width), style=style)
        if covers and not narrow:
            text.append(" " + fit(covers, room, glyphs.more).rstrip(), style="dim" if color else "")
        elif covers:
            text.append("\n" + " " * (len(glyphs.dot) + 1), style="")
            text.append(
                fit(covers, width - len(glyphs.dot) - 1, glyphs.more), style="dim" if color else ""
            )
    return text


def notice_lines(info: HomeInfo, glyphs: Glyphs, color: bool) -> Text:
    text = Text()
    for notice in info.notices:
        text.append(f"{glyphs.warn} ", style="yellow" if color else "")
        text.append(f"{notice.text} {glyphs.arrow} ")
        text.append(notice.command, style="bold cyan" if color else "")
        text.append("\n")
    text.rstrip()
    return text


def plain_status(info: HomeInfo, statuses: Mapping[str, AgentStatus]) -> str:
    lines = [f"{t('home.agents')}:"]
    for name in info.agents:
        status = statuses.get(name, AgentStatus(name, "unchecked"))
        covers = ", ".join(info.coverage.get(name, ()))
        lines.append(
            f"- {name}: {state_text(status)}."
            + (f" {t('home.covers')}: {covers}." if covers else "")
        )
    for notice in info.notices:
        lines.append(f"{t('home.notice')}: {notice.text} {t('home.run_this')}: {notice.command}")
    return "\n".join(lines)


def plain_header(info: HomeInfo, statuses: Mapping[str, AgentStatus], agents: bool = True) -> str:
    lines = [
        f"Timonel {info.version}. {t('home.tagline')}.",
        f"{t('home.profile')}: {info.profile}. {t('home.mode')}: {t(f'home.mode.{info.mode}')}.",
        place_line(info.place) + ".",
    ]
    if info.spend_today is not None:
        lines.append(f"{t('home.spend_today')}: ${info.spend_today:.2f}. {info.budget}".rstrip())
    if agents:
        lines.append(plain_status(info, statuses))
    return "\n".join(lines)


def glyphs_for(config: Config, console: Console) -> Glyphs:
    style = pick_logo(config.tui.logo, console)
    return UNICODE if style != "ascii" and unicode_ok(console.file) else ASCII


def top_block(info: HomeInfo, config: Config, console: Console) -> RenderableType:
    if config.tui.plain:
        return Text(plain_header(info, {}, agents=False))
    style = pick_logo(config.tui.logo, console)
    glyphs = glyphs_for(config, console)
    color = style == "color"
    brass = BRASS.get(config.tui.theme, BRASS["auto"]) if color else ""
    if config.tui.compact or style == "none":
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"{glyphs.helm} ", style=brass)
        line.append(f"Timonel {info.version}", style="bold" if color else "")
        line.append(
            f" {glyphs.sep} {info.profile} {glyphs.sep} {t(f'home.mode.{info.mode}')} {glyphs.sep} "
            + place_line(info.place)
        )
        return line
    logo = logo_block(style, config.tui.theme) or Text("")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(ratio=1)
    grid.add_row(logo, info_lines(info, glyphs, color))
    return grid


def status_block(
    info: HomeInfo, statuses: Mapping[str, AgentStatus], config: Config, console: Console
) -> RenderableType:
    if config.tui.plain:
        return Text(plain_status(info, statuses))
    glyphs = glyphs_for(config, console)
    color = pick_logo(config.tui.logo, console) == "color"
    rule = Text(glyphs.rule * max(console.width, 1), style="dim" if color else "", no_wrap=True)
    parts: list[RenderableType] = [rule, Text(t("home.agents"), style="bold" if color else "")]
    parts.append(agents_table(info, statuses, glyphs, color, console.width))
    if info.notices:
        parts.append(notice_lines(info, glyphs, color))
    return Group(*parts)


def header(
    info: HomeInfo,
    statuses: Mapping[str, AgentStatus],
    config: Config,
    console: Console,
) -> RenderableType:
    if config.tui.plain:
        return Text(plain_header(info, statuses))
    return Group(top_block(info, config, console), status_block(info, statuses, config, console))


def render_text(
    renderable: RenderableType, width: int, color: bool, ascii_only: bool = False
) -> str:
    from io import StringIO

    buffer = StringIO()
    console = Console(
        file=buffer,
        width=width,
        force_terminal=color,
        color_system="truecolor" if color else None,
        no_color=not color,
        highlight=False,
        legacy_windows=False,
    )
    console.print(renderable)
    text = buffer.getvalue()
    return text.encode("ascii", "replace").decode() if ascii_only else text


def status_from_health(health: AgentHealth, now: float) -> AgentStatus:
    if not health.installed:
        state: State = "unavailable"
    elif health.auth == "missing":
        state = "login"
    elif health.auth == "unknown":
        state = "login_unknown"
    else:
        state = "ready"
    return AgentStatus(health.name, state, health.version, health.auth_detail, now)


def cache_path() -> Path:
    return data_dir() / CACHE_FILE


def read_cache(path: Path, max_age_s: float, now: float) -> dict[str, AgentStatus]:
    if max_age_s <= 0:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found: dict[str, AgentStatus] = {}
    for name, item in raw.items() if isinstance(raw, dict) else ():
        try:
            status = AgentStatus(
                name=str(name),
                state=item["state"],
                version=str(item.get("version", "")),
                detail=str(item.get("detail", "")),
                checked_at=float(item["checked_at"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if status.state in STATE_STYLE and 0 <= now - status.checked_at <= max_age_s:
            found[status.name] = status
    return found


def write_cache(path: Path, statuses: Mapping[str, AgentStatus]) -> None:
    keep = {
        name: {key: value for key, value in asdict(status).items() if key != "name"}
        for name, status in statuses.items()
        if status.state not in ("checking", "unchecked", "no_answer")
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(keep, indent=1), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


Inspect = Callable[..., AgentHealth]


class StatusBoard:
    def __init__(
        self,
        config: Config,
        names: tuple[str, ...],
        *,
        environ: Mapping[str, str] | None = None,
        path: Path | None = None,
        inspect: Inspect = inspect_agent,
        clock: Callable[[], float] = time.time,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.names = names
        self.environ = dict(os.environ if environ is None else environ)
        self.path = path or cache_path()
        self.inspect = inspect
        self.clock = clock
        self.on_change = on_change
        self.lock = threading.Lock()
        cached = read_cache(self.path, config.tui.status_cache_s, clock())
        self.statuses: dict[str, AgentStatus] = {
            name: cached.get(name, AgentStatus(name, "unchecked")) for name in names
        }
        self.done = threading.Event()

    def snapshot(self) -> dict[str, AgentStatus]:
        with self.lock:
            return dict(self.statuses)

    def set(self, status: AgentStatus) -> None:
        with self.lock:
            self.statuses[status.name] = status
            pending = any(item.state == "checking" for item in self.statuses.values())
            if not pending:
                write_cache(self.path, self.statuses)
        if not pending:
            self.done.set()
        if self.on_change is not None:
            self.on_change()

    def check_one(self, name: str) -> None:
        agent = self.config.agents[name]
        try:
            health = self.inspect(agent, self.config, models=False, environ=self.environ)
        except Exception as error:
            self.set(AgentStatus(name, "no_answer", detail=type(error).__name__))
            return
        self.set(status_from_health(health, self.clock()))

    def expire(self, name: str) -> None:
        with self.lock:
            still = self.statuses[name].state == "checking"
        if still:
            self.set(AgentStatus(name, "no_answer", detail="timeout"))

    def start(self, force: bool = False) -> list[threading.Thread]:
        stale = [
            name
            for name, status in self.snapshot().items()
            if force or status.state in ("unchecked", "no_answer")
        ]
        if not stale:
            self.done.set()
            return []
        self.done.clear()
        with self.lock:
            for name in stale:
                self.statuses[name] = replace(self.statuses[name], state="checking")
        threads = []
        for name in stale:
            worker = threading.Thread(target=self.check_one, args=(name,), daemon=True)
            timer = threading.Timer(self.config.tui.check_timeout_s, self.expire, args=(name,))
            timer.daemon = True
            worker.start()
            timer.start()
            threads.append(worker)
        if self.on_change is not None:
            self.on_change()
        return threads

    def wait(self, timeout: float) -> bool:
        return self.done.wait(timeout)


def help_text() -> str:
    from timonel.shell import SLASH_COMMANDS

    lines = [t("home.help.intro"), ""]
    lines.extend(f"  /{name:<9} {t(f'home.cmd.{name}')}" for name in SLASH_COMMANDS)
    lines += ["", t("home.help.cli")]
    return "\n".join(lines)
