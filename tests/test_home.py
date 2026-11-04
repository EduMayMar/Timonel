import os
import re
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from timonel import home, i18n
from timonel.cli import main
from timonel.config import Config, validate_text
from timonel.doctor import AgentHealth

SNAPSHOTS = Path(__file__).parent / "snapshots"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def english() -> object:
    previous = i18n.current()
    i18n.set_language("en")
    yield
    i18n.set_language(previous)


def sample_config(extra: str = "") -> Config:
    return validate_text("budgets:\n  - {period: day, max_usd: 5}\n" + extra, "routes.yaml")


def sample_info(config: Config, statuses: dict[str, home.AgentStatus]) -> home.HomeInfo:
    place = home.Place(
        cwd=Path("/work/projects/shop-api"),
        repo=Path("/work/projects/shop-api"),
        branch="main",
        dirty=True,
    )
    return home.HomeInfo(
        version="9.9.9",
        profile="balanced",
        mode="simple",
        place=place,
        agents=("claude", "codex", "copilot", "deepseek"),
        coverage=home.coverage(config),
        spend_today=1.25,
        budget="budget left: $3.75 of $5.00 per day",
        notices=(
            home.Notice(
                "this project's .timonel.yaml is ignored until you trust it", "timonel trust"
            ),
            home.Notice("codex needs you to log in", "codex login"),
        ),
    )


STATUSES = {
    "claude": home.AgentStatus("claude", "ready", "2.1.317"),
    "codex": home.AgentStatus("codex", "login"),
    "copilot": home.AgentStatus("copilot", "checking"),
    "deepseek": home.AgentStatus("deepseek", "unavailable"),
}


def render(width: int, style: str, plain: bool = False) -> str:
    config = sample_config()
    config = replace(
        config, tui=replace(config.tui, logo=style if style != "auto" else "color", plain=plain)
    )
    info = sample_info(config, STATUSES)
    from io import StringIO

    from rich.console import Console

    color = style == "color"
    console = Console(
        file=StringIO(),
        width=width,
        force_terminal=color,
        color_system="truecolor" if color else None,
        no_color=not color,
    )
    return home.render_text(
        home.header(info, STATUSES, config, console), width, color, ascii_only=style == "ascii"
    )


CASES = [(width, style) for width in (60, 80, 120) for style in ("color", "mono", "ascii")]


@pytest.mark.parametrize(("width", "style"), CASES)
def test_header_snapshots(width: int, style: str) -> None:
    text = render(width, style)
    target = SNAPSHOTS / f"header-{width}-{style}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        target.write_text(text, encoding="utf-8")
    assert text == target.read_text(encoding="utf-8")
    for line in ANSI.sub("", text).splitlines():
        assert len(line) <= width, line
    if style == "ascii":
        assert text.isascii()
    if style != "color":
        assert "\x1b[" not in text
    plain = ANSI.sub("", text)
    for state in ("ready", "needs login", "checking...", "not installed"):
        assert state in plain
    assert "timonel trust" in plain and "codex login" in plain


def test_plain_mode_has_no_boxes_or_symbols() -> None:
    text = render(80, "mono", plain=True)
    assert "█" not in text and "●" not in text and "─" not in text
    assert "- codex: needs login." in text
    assert "Notice: codex needs you to log in run: codex login" in text


def test_status_never_relies_on_color_only() -> None:
    text = ANSI.sub("", render(80, "color"))
    for status in STATUSES.values():
        assert home.state_text(status) in text


def health(
    name: str, installed: bool = True, auth: str = "ok", version: str = "1.0"
) -> AgentHealth:
    return AgentHealth(
        name,
        name,
        installed,
        f"/bin/{name}" if installed else None,
        version,
        auth,
        "",
        "subscription",
        True,
    )  # type: ignore[arg-type]


def test_status_board_updates_asynchronously_and_caches(tmp_path: Path) -> None:
    config = sample_config()
    release = threading.Event()
    changes: list[int] = []

    def inspect(agent: object, config: object, **kwargs: object) -> AgentHealth:
        name = agent.name  # type: ignore[attr-defined]
        if name == "codex":
            release.wait(5)
            return health(name, auth="missing")
        return health(name)

    cache = tmp_path / "status.json"
    board = home.StatusBoard(
        config,
        ("claude", "codex"),
        path=cache,
        inspect=inspect,
        on_change=lambda: changes.append(1),
    )
    assert board.snapshot()["claude"].state == "unchecked"
    board.start()
    deadline = time.time() + 5
    while board.snapshot()["claude"].state != "ready" and time.time() < deadline:
        time.sleep(0.02)
    assert board.snapshot()["claude"].state == "ready"
    assert board.snapshot()["codex"].state == "checking"
    assert not cache.exists()
    release.set()
    assert board.wait(5)
    assert board.snapshot()["codex"].state == "login"
    assert changes
    again = home.StatusBoard(config, ("claude", "codex"), path=cache, inspect=inspect)
    assert again.snapshot()["codex"].state == "login"
    assert again.start() == []


def test_status_board_timeout_errors_and_expired_cache(tmp_path: Path) -> None:
    config = sample_config("tui: {check_timeout_s: 1, status_cache_s: 60}\n")
    stuck = threading.Event()

    def inspect(agent: object, config: object, **kwargs: object) -> AgentHealth:
        name = agent.name  # type: ignore[attr-defined]
        if name == "codex":
            stuck.wait(10)
        if name == "copilot":
            raise RuntimeError("boom")
        return health(name, installed=name != "deepseek")

    cache = tmp_path / "status.json"
    board = home.StatusBoard(
        config, ("codex", "copilot", "deepseek"), path=cache, inspect=inspect, clock=lambda: 1000.0
    )
    board.start()
    assert board.wait(5)
    snap = board.snapshot()
    assert snap["codex"].state == "no_answer" and snap["codex"].detail == "timeout"
    assert snap["copilot"].state == "no_answer"
    assert snap["deepseek"].state == "unavailable"
    assert home.read_cache(cache, 60, 1000.0 + 61) == {}
    assert "deepseek" in home.read_cache(cache, 60, 1000.0 + 30)
    assert "codex" not in home.read_cache(cache, 60, 1000.0 + 30)
    stuck.set()
    deadline = time.time() + 5
    while board.snapshot()["codex"].state != "ready" and time.time() < deadline:
        time.sleep(0.02)
    assert board.snapshot()["codex"].state == "ready"
    cache.write_text("{broken", encoding="utf-8")
    assert home.read_cache(cache, 60, 1000.0) == {}
    assert home.read_cache(cache, 0, 1000.0) == {}


def test_notices_point_to_the_exact_command(home_dir: Path) -> None:
    config = sample_config()
    place = home.Place(cwd=home_dir)
    statuses = {
        "codex": home.AgentStatus("codex", "login", "0.100.0"),
        "deepseek": home.AgentStatus("deepseek", "login"),
    }
    found = {notice.command for notice in home.notices(config, place, statuses)}
    assert {
        "timonel start",
        "/git-init",
        "codex login",
        "timonel keys set DEEPSEEK_API_KEY",
    } <= found
    assert any(command.startswith("npm install -g @openai/codex") for command in found)
    quiet = replace(config, tui=replace(config.tui, notices=()))
    assert home.notices(quiet, place, statuses) == ()


def test_coverage_groups_tiers_by_model() -> None:
    config = validate_text(
        "tiers:\n  1: [{agent: claude, model: sonnet}]\n  2: [{agent: claude, model: sonnet}]\n"
        "  3: [{agent: claude, model: opus}]\n  4: [{agent: claude, model: opus}]\n"
    )
    assert home.coverage(config)["claude"][:2] == ("T1-T2 sonnet", "T3-T4 opus")
    assert home.tier_span([1, 3, 4]) == "T1,T3-T4"


@pytest.fixture
def home_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("TIMONEL_HOME", str(folder))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_bare_timonel_without_terminal_prints_header_and_help(
    home_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--set", "tui.check_on_start=false"]) == 0
    out = capsys.readouterr().out
    assert "Timonel" in out and "Agents" in out
    assert "/plan" in out and "timonel --help" in out
    assert main(["home", "--once", "--width", "60"]) == 0
    once = capsys.readouterr().out
    assert "/plan" not in once and "Agents" in once
    assert all(len(line) <= 60 for line in once.splitlines())
    assert main(["--no-color", "home", "--once", "--plain"]) == 0
    plain = capsys.readouterr().out
    assert "█" not in plain and "Agents:" in plain


def test_missing_prompt_toolkit_degrades_to_static(
    home_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import builtins

    from timonel import shell

    real_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("prompt_toolkit"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", blocked)
    from rich.console import Console

    config = sample_config("tui: {check_on_start: false}\n")
    assert shell.interactive(config, home_dir, Console(width=80)) == 0
    out = capsys.readouterr().out
    assert "prompt_toolkit" in out and "/exit" in out


def test_compact_and_logo_none_are_one_line_headers() -> None:
    from io import StringIO

    from rich.console import Console

    for extra in ("tui: {compact: true}\n", "tui: {logo: none}\n"):
        config = sample_config(extra)
        console = Console(file=StringIO(), width=60, no_color=True)
        text = home.render_text(
            home.top_block(sample_info(config, STATUSES), config, console), 60, False
        )
        assert len(text.strip().splitlines()) == 1
        assert "Timonel 9.9.9" in text and "balanced" in text


def test_non_utf_console_falls_back_to_ascii(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

    class Legacy:
        encoding = "cp1252"

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            return None

    console = Console(file=Legacy(), width=80, force_terminal=True)  # type: ignore[arg-type]
    assert home.pick_logo("auto", console) == "ascii"
    monkeypatch.setenv("TERM", "dumb")
    assert not home.unicode_ok(type("S", (), {"encoding": "utf-8"})())


def test_folder_names_cannot_inject_terminal_sequences() -> None:
    assert "\x1b" not in home.short_path(Path("/tmp/x\x1b]0;PWN\x07y"))
    assert "\x1b" not in home.fit("a\x1b[2Jb", 20)


def test_notice_list_is_configurable(home_dir: Path) -> None:
    config = sample_config("tui: {notices: [login]}\n")
    place = home.Place(cwd=home_dir)
    found = home.notices(config, place, {"codex": home.AgentStatus("codex", "login")})
    assert [notice.command for notice in found] == ["codex login"]


def test_a_stale_timeout_does_not_mark_a_newer_check(tmp_path: Path) -> None:
    config = sample_config("tui: {check_timeout_s: 1}\n")
    slow = {"value": 0.0}

    def inspect(agent: object, config: object, **kwargs: object) -> AgentHealth:
        time.sleep(slow["value"])
        return health(agent.name)  # type: ignore[attr-defined]

    board = home.StatusBoard(config, ("claude",), path=tmp_path / "s.json", inspect=inspect)
    board.start()
    assert board.wait(5)
    time.sleep(0.7)
    slow["value"] = 0.8
    board.start(force=True)
    time.sleep(0.5)
    assert board.snapshot()["claude"].state == "checking"
    assert board.wait(5)
    assert board.snapshot()["claude"].state == "ready"


def test_non_tty_subprocess_exits_zero_without_escape_codes(home_dir: Path) -> None:
    import subprocess
    import sys

    env = {**os.environ, "TIMONEL_HOME": str(home_dir / "home"), "TIMONEL_LANG": "en"}
    for argv in ([], ["home", "--once"], ["--set", "tui.enabled=false"]):
        result = subprocess.run(
            [sys.executable, "-m", "timonel", "--set", "tui.check_on_start=false", *argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=home_dir,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "\x1b[" not in result.stdout
        assert "Agents" in result.stdout
