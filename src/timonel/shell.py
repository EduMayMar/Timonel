from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.text import Text

from timonel import commands, home, wizard
from timonel.config import Config, TuiKeys
from timonel.doctor import default_names
from timonel.gitops import GitError
from timonel.i18n import t
from timonel.ledger import data_dir
from timonel.redact import Redactor

SLASH_COMMANDS = (
    "plan",
    "run",
    "agents",
    "doctor",
    "stats",
    "config",
    "profile",
    "mode",
    "diff",
    "apply",
    "discard",
    "resume",
    "ui",
    "learn",
    "git-init",
    "new",
    "help",
    "clear",
    "exit",
)
MENU_STYLE = {
    "bottom-toolbar": "noreverse",
    "completion-menu": "bg:#13233a #e8e2d0",
    "completion-menu.completion.current": "bg:#c08a2c #0d1726",
    "completion-menu.meta.completion": "bg:#13233a #9aa6b6",
    "completion-menu.meta.completion.current": "bg:#c08a2c #0d1726",
}
TOKEN_LIKE = re.compile(r"(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}")
PROFILE_ORDER = ("cheap", "balanced", "max")
PASSTHROUGH: dict[str, tuple[str, ...]] = {
    "agents": ("agents",),
    "doctor": ("doctor",),
    "stats": ("stats",),
    "config": ("config",),
    "diff": ("diff",),
    "apply": ("apply",),
    "discard": ("discard",),
    "resume": ("resume",),
    "learn": ("learn",),
}
DEFAULT_ARGS: dict[str, tuple[str, ...]] = {"agents": ("list",), "config": ("list",)}
TAKES_ARGUMENTS = ("plan", "run", "config", "profile", "mode", "learn")
NEEDS_RUN_ID = ("diff", "apply", "discard", "resume")


class Shell:
    def __init__(
        self,
        config: Config,
        cwd: Path,
        console: Console,
        *,
        ask: Callable[[str], str] | None = None,
        board: home.StatusBoard | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.cwd = cwd
        self.console = console
        self.environ = dict(os.environ if environ is None else environ)
        self.ask = ask or (lambda question: input(question))
        self.profile = config.default_profile
        self.mode = config.mode
        self.last_run: str | None = None
        self.started = time.time()
        self.unbound: dict[str, str] = {}
        self.board = board or home.StatusBoard(
            config, tuple(sorted(default_names(config))), environ=self.environ
        )
        self.redactor = Redactor(
            self.environ,
            patterns=config.privacy.redact_patterns,
            secret_names=config.privacy.secret_env_names,
        )
        self.info = home.gather(config, cwd, self.board.snapshot(), self.profile, self.mode)

    def looks_secret(self, line: str) -> bool:
        return self.redactor(line) != line or bool(TOKEN_LIKE.search(line))

    def refresh_info(self) -> None:
        self.info = home.gather(
            self.config, self.cwd, self.board.snapshot(), self.profile, self.mode
        )

    def header(self, agents: bool = True) -> RenderableType:
        statuses = self.board.snapshot()
        info = replace(
            self.info,
            profile=self.profile,
            mode=self.mode,
            notices=home.notices(self.config, self.info.place, statuses),
        )
        if agents:
            return home.header(info, statuses, self.config, self.console)
        return home.top_block(info, self.config, self.console)

    def panel(self) -> RenderableType:
        statuses = self.board.snapshot()
        info = replace(self.info, notices=home.notices(self.config, self.info.place, statuses))
        return home.status_block(info, statuses, self.config, self.console)

    def status_line(self) -> str:
        statuses = self.board.snapshot()
        parts = [
            f"{name} {home.state_text(statuses.get(name, home.AgentStatus(name, 'unchecked')))}"
            for name in self.info.agents
        ]
        return " · ".join(parts)

    def footer(self) -> str:
        keys = replace(self.config.tui.keys, **self.unbound)
        return t(
            "home.footer",
            profile=self.profile,
            mode=t(f"home.mode.{self.mode}"),
            profile_key=key_label(keys.profile),
            mode_key=key_label(keys.mode),
            help_key=key_label(keys.help),
            exit_key=key_label(keys.exit),
        )

    def cycle_profile(self) -> str:
        names = [name for name in PROFILE_ORDER if name in self.config.profiles] or sorted(
            self.config.profiles
        )
        index = names.index(self.profile) if self.profile in names else -1
        self.profile = names[(index + 1) % len(names)]
        return self.profile

    def toggle_mode(self) -> str:
        self.mode = "expert" if self.mode == "simple" else "simple"
        return self.mode

    def say(self, text: str) -> None:
        self.console.print(text, markup=False, highlight=False)

    def invoke(self, argv: Sequence[str]) -> int:
        from timonel import cli

        try:
            args = cli.build_parser().parse_args(list(argv))
        except SystemExit as stop:
            return int(stop.code or 0)
        try:
            return int(args.handler(args, self.config))
        except KeyboardInterrupt:
            self.say(t("common.aborted"))
            return 130
        except Exception as error:
            cli.report_error(error)
            return 1

    def handle(self, line: str) -> bool:
        text = line.strip()
        if not text:
            return True
        if not text.startswith("/"):
            self.task(text, run=True)
            return True
        try:
            parts = shlex.split(text[1:], posix=os.name != "nt")
        except ValueError as error:
            self.say(t("home.bad_quotes", error=str(error)))
            return True
        if not parts:
            return True
        name, rest = parts[0].lower(), parts[1:]
        if name not in SLASH_COMMANDS:
            close = difflib.get_close_matches(name, SLASH_COMMANDS, n=1)
            hint = t("home.did_you_mean", command=f"/{close[0]}") if close else ""
            self.say(t("home.unknown_command", command=f"/{name}") + hint)
            return True
        return self.slash(name, rest)

    def slash(self, name: str, rest: list[str]) -> bool:
        if name == "exit":
            return False
        if name == "help":
            self.say(home.help_text())
        elif name == "clear":
            self.refresh_info()
            self.console.clear()
            self.console.print(self.header())
        elif name in ("plan", "run"):
            if not rest:
                self.say(t("home.needs_task", command=f"/{name}"))
            else:
                self.task(" ".join(rest), run=name == "run")
        elif name == "profile":
            self.choose_profile(rest)
        elif name == "mode":
            if rest and rest[0] in ("simple", "expert"):
                if self.invoke(("mode", rest[0])) == 0:
                    self.mode = rest[0]
            else:
                self.say(t("home.mode_now", mode=t(f"home.mode.{self.mode}")))
        elif name == "ui":
            self.invoke(("ui", "--repo", str(self.cwd)))
        elif name == "git-init":
            self.git_init()
        elif name == "new":
            self.started = time.time()
            self.say(t("home.new_started"))
        else:
            self.passthrough(name, rest)
        return True

    def passthrough(self, name: str, rest: list[str]) -> None:
        if name in NEEDS_RUN_ID and not rest:
            if self.last_run is None:
                self.say(t("home.needs_run_id", command=f"/{name}"))
                return
            rest = [self.last_run]
        argv = [*PASSTHROUGH[name], *(rest or DEFAULT_ARGS.get(name, ()))]
        self.invoke(argv)
        if name == "doctor":
            self.board.start(force=True)
        if name in ("apply", "discard"):
            self.refresh_info()

    def choose_profile(self, rest: list[str]) -> None:
        if not rest:
            self.say(
                t(
                    "home.profiles",
                    current=self.profile,
                    names=", ".join(sorted(self.config.profiles)),
                )
            )
            return
        if rest[0] not in self.config.profiles:
            self.say(t("home.unknown_profile", name=rest[0]))
            return
        self.profile = rest[0]
        self.say(t("home.profile_set", name=self.profile))

    def git_init(self) -> bool:
        current = home.place_of(self.cwd)
        if current.repo is not None:
            self.refresh_info()
            self.say(t("home.already_git", path=str(current.repo)))
            return True
        try:
            created = commands.offer_git_init(self.cwd, ask=self.ask, say=self.say)
        except GitError as error:
            self.say(str(error))
            return False
        finally:
            self.refresh_info()
        return created

    def task(self, text: str, *, run: bool) -> None:
        from timonel import cli

        engine = cli.make_engine(self.config, live=True)
        try:
            plan = engine.plan(text, profile=self.profile, cwd=self.cwd)
        except Exception as error:
            cli.report_error(error)
            return
        cli.print_plan(plan)
        if not run:
            return
        if home.place_of(self.cwd).repo is None:
            self.say(t("home.run_needs_git"))
            if not self.git_init():
                return
        if self.config.tui.confirm_run:
            answer = self.ask(t("home.confirm_run") + " [Y/n] ").strip()
            if answer and not wizard.yes(answer):
                self.say(t("common.aborted"))
                return
        self.execute(engine, text, plan)

    def execute(self, engine: Any, text: str, plan: Any) -> None:
        from timonel import cli
        from timonel.engine import RunOptions

        options = RunOptions(
            task=text, repo=self.cwd, profile=self.profile, plan=plan, since=self.started
        )
        try:
            run_id = engine.start(options)
        except Exception as error:
            cli.report_error(error)
            return
        self.last_run = run_id
        self.say(t("home.run_started", id=run_id))
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                outcome["record"] = engine.run(options, run_id)
            except Exception as error:
                outcome["error"] = error

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                worker.join(0.2)
        except KeyboardInterrupt:
            self.say(t("home.cancelling", id=run_id))
            engine.cancel(run_id)
            while worker.is_alive():
                try:
                    worker.join(0.5)
                except KeyboardInterrupt:
                    self.say(t("home.still_cancelling"))
        self.refresh_info()
        if "error" in outcome:
            cli.report_error(outcome["error"])
            return
        cli.print_result(outcome.get("record") or engine.require(run_id))


def key_label(name: str) -> str:
    labels = {"s-tab": "Shift+Tab", "c-d": "Ctrl+D", "c-c": "Ctrl+C", "escape": "Esc"}
    if name in labels:
        return labels[name]
    if name.startswith("c-"):
        return f"Ctrl+{name[2:].upper()}"
    if name.startswith("s-"):
        return f"Shift+{name[2:].capitalize()}"
    return name.upper() if len(name) <= 3 else name


class SecretFilteredHistory:
    def __init__(self, path: Path, keep: int, looks_secret: Callable[[str], bool]) -> None:
        from prompt_toolkit.history import FileHistory

        class Filtered(FileHistory):
            def store_string(inner, string: str) -> None:
                if looks_secret(string) or keep <= 0:
                    return
                super().store_string(string)

        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch(mode=0o600)
        with suppress(OSError):
            path.chmod(0o600)
        trim_history(path, keep)
        self.history = Filtered(str(path))


def trim_history(path: Path, keep: int) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    starts = [index for index, line in enumerate(lines) if line.startswith("# ")]
    if keep <= 0 and lines:
        path.write_text("", encoding="utf-8")
    elif len(starts) > keep:
        path.write_text("".join(lines[starts[-keep] :]), encoding="utf-8")


def completions(text: str) -> Iterable[tuple[str, str]]:
    if not text.startswith("/") or " " in text:
        return []
    word = text[1:].lower()
    return [(f"/{name}", t(f"home.cmd.{name}")) for name in SLASH_COMMANDS if name.startswith(word)]


def ansi(renderable: RenderableType, console: Console) -> str:
    width = shutil.get_terminal_size((console.width, 24)).columns
    color = not console.no_color and console.color_system is not None
    return home.render_text(renderable, width, color)


def interactive(config: Config, cwd: Path, console: Console) -> int:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit import prompt as plain_prompt
        from prompt_toolkit.application import get_app
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.filters import Condition, completion_is_selected
        from prompt_toolkit.formatted_text import ANSI
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except ImportError:
        return static(config, cwd, console, reason=t("home.no_prompt_toolkit"))
    shell_ref: dict[str, Shell] = {}
    session_ref: dict[str, Any] = {}

    def redraw() -> None:
        app = session_ref.get("session")
        if app is not None and app.app.is_running:
            app.app.invalidate()

    board = home.StatusBoard(config, tuple(sorted(default_names(config))), on_change=redraw)
    shell = Shell(config, cwd, console, board=board)
    shell_ref["shell"] = shell
    if config.tui.clear_screen and console.is_terminal:
        console.clear()
    console.print(shell.header(agents=False))
    if config.tui.check_on_start:
        board.start()

    class SlashCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Iterable[Completion]:
            text = document.text_before_cursor
            for value, meta in completions(text):
                yield Completion(value, start_position=-len(text), display_meta=meta)

    bindings = KeyBindings()
    state = {"first": True}

    def bind(field: str, key: str, action: Callable[[Any], None], **options: Any) -> None:
        try:
            bindings.add(key, **options)(action)
        except (ValueError, KeyError):
            default = getattr(TuiKeys(), field)
            shell.unbound[field] = default
            bindings.add(default, **options)(action)

    def next_profile(event: Any) -> None:
        shell.cycle_profile()
        event.app.invalidate()

    def next_mode(event: Any) -> None:
        shell.toggle_mode()
        event.app.invalidate()

    def show_help(event: Any) -> None:
        from prompt_toolkit.application import run_in_terminal

        run_in_terminal(lambda: shell.say(home.help_text()))

    def leave(event: Any) -> None:
        event.app.exit(exception=EOFError())

    @bindings.add("enter", filter=completion_is_selected)
    def run_selected(event: Any) -> None:
        buffer = event.current_buffer
        selected = buffer.complete_state
        if selected is not None and selected.current_completion is not None:
            buffer.apply_completion(selected.current_completion)
        if buffer.text.strip().lstrip("/") in TAKES_ARGUMENTS:
            buffer.insert_text(" ")
            return
        buffer.validate_and_handle()

    empty = Condition(lambda: not get_app().current_buffer.text)
    keys = config.tui.keys
    bind("profile", keys.profile, next_profile)
    bind("mode", keys.mode, next_mode)
    bind("help", keys.help, show_help)
    bind("exit", keys.exit, leave, filter=empty)

    plain = console.no_color or console.color_system is None
    dim, reset = ("", "") if plain else ("\x1b[2m", "\x1b[0m")

    def message() -> Any:
        glyphs = home.glyphs_for(config, console)
        width = shutil.get_terminal_size((80, 24)).columns
        top = ansi(shell.panel(), console) if state["first"] else ""
        return ANSI(f"{top}{dim}{glyphs.rule * width}{reset}\n{glyphs.prompt} ")

    def toolbar() -> Any:
        lines = Group(Text(shell.status_line(), style="dim"), Text(shell.footer(), style="dim"))
        return ANSI(ansi(lines, console).rstrip("\n"))

    history: Any = None
    if config.tui.history:
        history = SecretFilteredHistory(
            data_dir() / "home-history", config.tui.history_size, shell.looks_secret
        ).history
    session: Any = PromptSession(
        message=message,
        bottom_toolbar=toolbar,
        placeholder=ANSI(dim + (config.tui.placeholder or t("home.placeholder")) + reset),
        completer=SlashCompleter(),
        complete_while_typing=True,
        history=history,
        key_bindings=bindings,
        refresh_interval=config.tui.refresh_s,
        reserve_space_for_menu=4,
        style=Style.from_dict({"bottom-toolbar": "noreverse"} if plain else MENU_STYLE),
    )
    session_ref["session"] = session
    shell.ask = lambda question: plain_prompt(question)
    while True:
        try:
            line = session.prompt()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        state["first"] = False
        try:
            keep = shell.handle(line)
        except (KeyboardInterrupt, EOFError):
            shell.say(t("common.aborted"))
            continue
        if not keep:
            break
    console.print(t("home.bye"))
    return 0


def static(config: Config, cwd: Path, console: Console, reason: str = "") -> int:
    board = home.StatusBoard(config, tuple(sorted(default_names(config))))
    shell = Shell(config, cwd, console, board=board)
    console.print(shell.header())
    if reason:
        console.print(reason, markup=False)
    console.print(home.help_text(), markup=False, highlight=False)
    return 0
