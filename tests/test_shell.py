import os
import re
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from timonel import commands, gitops, i18n, shell
from timonel.home import StatusBoard

from .conftest import TEST_ROUTES, WINDOWS, Fakes, make_config, run_git

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[=>]")


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = i18n.current()
    i18n.set_language("en")
    yield
    i18n.set_language(previous)


@pytest.fixture
def env(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> Fakes:
    for key, value in fakes.env.items():
        monkeypatch.setenv(key, value)
    return fakes


def make_shell(
    cwd: Path, answers: list[str], extra: str = "", tui: str = ""
) -> tuple[shell.Shell, StringIO]:
    config = make_config(f"tui: {{check_on_start: false{', ' + tui if tui else ''}}}\n" + extra)
    buffer = StringIO()
    console = Console(file=buffer, width=120, no_color=True, highlight=False)
    pending = list(answers)
    board = StatusBoard(config, ("claude", "codex"), path=cwd / "status-cache.json")
    instance = shell.Shell(
        config, cwd, console, ask=lambda question: pending.pop(0) if pending else "", board=board
    )
    return instance, buffer


def test_free_text_plans_confirms_and_runs(
    env: Fakes, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance, _ = make_shell(repo, ["y"])
    assert instance.handle("add a greeting [[edit all:greet.py=print('hi')]]")
    out = capsys.readouterr().out
    assert "route" in out and "succeeded" in out
    assert instance.last_run is not None
    assert instance.handle("/diff")
    assert "greet.py" in capsys.readouterr().out
    assert instance.handle("/discard")
    assert "discarded" in capsys.readouterr().out.lower()


def test_declining_the_plan_runs_nothing(
    env: Fakes, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance, buffer = make_shell(repo, ["n"])
    instance.handle("/run add a file [[edit all:x.py=1]]")
    out = capsys.readouterr().out
    assert "route" in out and "Aborted" in buffer.getvalue()
    assert instance.last_run is None
    assert env.calls() == []


def test_plan_only_and_validation_messages(
    env: Fakes, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance, buffer = make_shell(repo, [])
    instance.handle("/plan explain app.py")
    assert "route" in capsys.readouterr().out
    instance.handle("/plan")
    instance.handle("/apply")
    instance.handle("/hepl")
    instance.handle('/run "unclosed')
    instance.handle("/profile")
    instance.handle("/profile nope")
    instance.handle("/profile max")
    instance.handle("/mode")
    instance.handle("   ")
    instance.handle("/")
    text = buffer.getvalue()
    assert "Write the task after /plan" in text
    assert "No run in this session yet" in text
    assert "Did you mean /help?" in text
    assert "Could not read the command" in text
    assert "There is no profile called nope." in text
    assert instance.profile == "max"
    assert "Mode: simple" in text
    assert instance.handle("/exit") is False


def test_slash_commands_reuse_the_cli_handlers(
    env: Fakes, repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    instance, _ = make_shell(repo, [])
    original = instance.invoke

    def spy(argv: Any) -> int:
        seen.append(list(argv))
        return 0 if argv[0] in ("ui", "mode") else original(argv)

    monkeypatch.setattr(instance, "invoke", spy)
    restarted: list[bool] = []
    monkeypatch.setattr(instance.board, "start", lambda force=False: restarted.append(force) or [])
    for line in (
        "/agents",
        "/stats",
        "/config get mode",
        "/learn",
        "/doctor",
        "/ui",
        "/mode expert",
    ):
        instance.handle(line)
    assert seen == [
        ["agents", "list"],
        ["stats"],
        ["config", "get", "mode"],
        ["learn"],
        ["doctor"],
        ["ui", "--repo", str(repo)],
        ["mode", "expert"],
    ]
    assert restarted == [True]
    assert instance.mode == "expert"
    out = capsys.readouterr().out
    assert "simple" in out


def test_repo_created_elsewhere_is_noticed_and_never_reinitialized(
    env: Fakes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "later"
    folder.mkdir()
    instance, _ = make_shell(folder, ["y"], tui="confirm_run: false")
    run_git(folder, "init", "-q")
    (folder / "a.py").write_text("1\n", encoding="utf-8")
    run_git(folder, "add", "-A")
    run_git(folder, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "mine")
    called: list[object] = []
    monkeypatch.setattr(commands, "offer_git_init", lambda *a, **k: called.append(a) or True)
    instance.handle("add a file [[edit all:b.py=1]]")
    assert called == []
    assert instance.last_run is not None
    assert run_git(folder, "log", "--oneline").count("\n") == 1


def test_confirm_run_false_runs_without_asking(env: Fakes, repo: Path) -> None:
    def never(question: str) -> str:
        raise AssertionError("asked")

    instance, _ = make_shell(repo, [], tui="confirm_run: false")
    instance.ask = never
    instance.handle("add [[edit all:c.py=1]]")
    assert instance.last_run is not None


def test_shortcuts_footer_and_completion() -> None:
    assert [value for value, _ in shell.completions("/re")] == ["/resume"]
    assert shell.completions("hello") == []
    assert shell.completions("/plan x") == []
    assert shell.key_label("s-tab") == "Shift+Tab"
    assert shell.key_label("c-p") == "Ctrl+P"
    assert shell.key_label("f2") == "F2"
    instance, _ = make_shell(Path.cwd(), [])
    assert instance.cycle_profile() == "max"
    assert instance.cycle_profile() == "cheap"
    assert instance.toggle_mode() == "expert"
    footer = instance.footer()
    assert "Shift+Tab" in footer and "F2" in footer and "Ctrl+D" in footer and "cheap" in footer
    instance.unbound["profile"] = "s-tab"
    assert "Shift+Tab" in instance.footer()


def test_history_never_keeps_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghijklmnopqrstuvwxyz123456")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "plainvalue-without-prefix-0042")
    instance, _ = make_shell(tmp_path, [])
    path = tmp_path / "history"
    path.write_text("", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o644)
    history = shell.SecretFilteredHistory(path, 3, instance.looks_secret).history
    history.store_string("fix the login bug")
    history.store_string("use sk-proj-abcdefghijklmnopqrstuvwxyz123456 for the test")
    history.store_string("token ghp_" + "a" * 36)
    history.store_string("try plainvalue-without-prefix-0042 here")
    history.store_string("key 3f9a7c1e5b2d4f6a8c0e9b7d5f3a1c2e")
    text = path.read_text(encoding="utf-8")
    assert "fix the login bug" in text
    assert "sk-proj" not in text and "ghp_" not in text
    assert "plainvalue" not in text and "3f9a7c1e" not in text
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    for index in range(6):
        history.store_string(f"task {index}")
    shell.trim_history(path, 3)
    kept = path.read_text(encoding="utf-8")
    assert "task 5" in kept and "task 2" not in kept
    shell.trim_history(path, 0)
    assert path.read_text(encoding="utf-8") == ""


def test_git_init_flow_with_gitignore_and_risky_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "main.py").write_text("print(1)\n", encoding="utf-8")
    (folder / ".env").write_text("SECRET=1\n", encoding="utf-8")
    said: list[str] = []
    answers = iter(["n"])
    assert not commands.offer_git_init(folder, ask=lambda q: next(answers), say=said.append)
    assert not (folder / ".git").exists()
    answers = iter(["y", "y"])
    assert commands.offer_git_init(folder, ask=lambda q: next(answers), say=said.append)
    assert ".env" in (folder / ".gitignore").read_text(encoding="utf-8")
    committed = run_git(folder, "ls-files")
    assert "main.py" in committed and ".env" not in committed
    assert run_git(folder, "log", "--oneline").strip()

    risky = tmp_path / "risky"
    risky.mkdir()
    (risky / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (risky / ".env").write_text("SECRET=1\n", encoding="utf-8")
    said.clear()
    answers = iter(["y", "n"])
    assert not commands.offer_git_init(risky, ask=lambda q: next(answers), say=said.append)
    assert any(".env" in line for line in said)
    assert not (risky / ".git").exists()
    assert gitops.is_sensitive("config/.env.production")
    assert not gitops.is_sensitive(".env.example")
    assert gitops.is_sensitive("certs/server.pem")
    for name in (
        ".ssh/id_ecdsa",
        ".aws/credentials",
        ".git-credentials",
        ".kube/config",
        "deploy/service-account.json",
        "infra/terraform.tfstate",
        ".envrc",
        "secrets.yaml",
    ):
        assert gitops.is_sensitive(name), name
    assert not gitops.is_sensitive("src/app.py")
    said.clear()
    assert not commands.offer_git_init(Path.home(), ask=lambda q: "y", say=said.append)
    assert any("will not create a repository" in line for line in said)
    assert gitops.too_broad(Path(Path.home().anchor))


def test_running_a_task_outside_git_offers_the_repository(
    env: Fakes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "app.py").write_text("print(1)\n", encoding="utf-8")
    instance, buffer = make_shell(folder, ["n"])
    instance.handle("add tests [[edit all:t.py=1]]")
    assert "route" in capsys.readouterr().out
    assert "needs a git repository" in buffer.getvalue()
    assert instance.last_run is None
    instance, buffer = make_shell(folder, ["y", "y", "y"])
    instance.handle("/git-init")
    assert (folder / ".git").exists()
    instance.handle("/git-init")
    assert "already in a git repository" in buffer.getvalue()


def spawn(argv: list[str], cwd: Path, env: dict[str, str]) -> Any:
    if WINDOWS:
        winpty = pytest.importorskip("winpty")
        return WindowsTerminal(
            winpty.PtyProcess.spawn(argv, cwd=str(cwd), env=env, dimensions=(40, 110))
        )
    return PosixTerminal(argv, cwd, env)


class PosixTerminal:
    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> None:
        import fcntl
        import pty
        import struct
        import termios

        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.chdir(cwd)
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 110, 0, 0))
        self.output = ""

    def read(self, timeout: float) -> None:
        import select

        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.output += chunk.decode("utf-8", "replace")

    def write(self, text: str) -> None:
        os.write(self.fd, text.encode())

    def wait(self, timeout: float) -> int | None:
        end = time.time() + timeout
        while time.time() < end:
            self.read(0.1)
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.pid = 0
                return os.waitstatus_to_exitcode(status)
        return None

    def close(self) -> None:
        import signal

        if self.pid:
            with suppress(OSError):
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
        with suppress(OSError):
            os.close(self.fd)


class WindowsTerminal:
    def __init__(self, process: Any) -> None:
        self.process = process
        self.output = ""

    def read(self, timeout: float) -> None:
        end = time.time() + timeout
        while time.time() < end and self.process.isalive():
            try:
                self.output += self.process.read(4096)
            except EOFError:
                return

    def write(self, text: str) -> None:
        self.process.write(text)

    def wait(self, timeout: float) -> int | None:
        end = time.time() + timeout
        while time.time() < end and self.process.isalive():
            self.read(0.1)
        if self.process.isalive():
            return None
        status = self.process.exitstatus
        return None if status is None else int(status)

    def close(self) -> None:
        if self.process.isalive():
            self.process.terminate(force=True)


def expect(terminal: Any, text: str, timeout: float = 30) -> str:
    end = time.time() + timeout
    start = getattr(terminal, "seen", 0)
    while time.time() < end:
        terminal.read(0.2)
        clean = ANSI.sub("", terminal.output)
        found = clean.find(text, start)
        if found >= 0:
            terminal.seen = found + len(text)
            return clean
    raise AssertionError(f"{text!r} not seen; output was:\n{ANSI.sub('', terminal.output)[-3000:]}")


@pytest.fixture
def terminals() -> Iterator[list[Any]]:
    opened: list[Any] = []
    yield opened
    for terminal in opened:
        terminal.close()


@pytest.fixture
def terminal_env(fakes: Fakes, home: Path, repo: Path) -> dict[str, str]:
    (home / "routes.yaml").write_text(
        TEST_ROUTES + "tui: {check_timeout_s: 10, history: true}\n", encoding="utf-8"
    )
    environment = {
        **fakes.env,
        "TIMONEL_HOME": str(home),
        "TERM": "xterm-256color",
        "PROMPT_TOOLKIT_NO_CPR": "1",
        "TIMONEL_LANG": "en",
        "PYTHONUTF8": "1",
        "PATH": os.pathsep.join(
            [
                str(fakes.bin_dir),
                str(Path(shutil.which("git") or "git").parent),
                str(Path(sys.executable).parent),
            ]
        ),
    }
    environment.pop("NO_COLOR", None)
    return environment


def test_terminal_session_task_slash_command_and_exit(
    terminal_env: dict[str, str], repo: Path, home: Path, terminals: list[Any]
) -> None:
    terminal = spawn([sys.executable, "-m", "timonel"], repo, terminal_env)
    terminals.append(terminal)
    expect(terminal, "Describe a task")
    expect(terminal, "ready")
    terminal.write("/re")
    expect(terminal, "continue an interrupted run")
    terminal.write("\x7f\x7f\x7f")
    terminal.write("/hel")
    expect(terminal, "this help")
    terminal.write("\t")
    time.sleep(0.5)
    terminal.write("\r")
    expect(terminal, "/git-init")
    terminal.write("\x03")
    terminal.write("/profile max\r")
    expect(terminal, "Profile for this session: max.")
    terminal.write("add a greeting [[edit all:greet.py=print('hi')]]\r")
    expect(terminal, "Run this plan?")
    terminal.write("\x03")
    expect(terminal, "Aborted")
    terminal.write("add a greeting [[edit all:greet.py=print('hi')]]\r")
    expect(terminal, "Run this plan?")
    terminal.write("y\r")
    expect(terminal, "succeeded", timeout=60)
    terminal.write("/exit\r")
    expect(terminal, "Bye.")
    assert terminal.wait(15) == 0
    history = (home / "home-history").read_text(encoding="utf-8")
    assert "add a greeting" in history


def test_terminal_ctrl_c_cancels_a_running_task(
    terminal_env: dict[str, str], repo: Path, home: Path, terminals: list[Any]
) -> None:
    terminal = spawn([sys.executable, "-m", "timonel"], repo, {**terminal_env, "FAKE_MODE": "hang"})
    terminals.append(terminal)
    expect(terminal, "Describe a task")
    terminal.write("add a slow change [[edit all:slow.py=1]]\r")
    expect(terminal, "Run this plan?")
    terminal.write("\r")
    expect(terminal, "Ctrl+C cancels it")
    time.sleep(1.5)
    terminal.write("\x03")
    expect(terminal, "Cancelling run")
    expect(terminal, "cancelled", timeout=60)
    from timonel.ledger import Ledger

    runs = Ledger(home / "ledger.sqlite").list_runs(limit=5)
    assert runs and runs[0]["status"] == "cancelled"
    terminal.write("\x04")
    expect(terminal, "Bye.")
    assert terminal.wait(15) == 0
