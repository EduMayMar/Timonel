from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from timonel import __version__, commands, gitops
from timonel.config import Config, ConfigError, load_config, user_config_path, validate_text
from timonel.doctor import AgentHealth, diagnose
from timonel.engine import Engine, RunOptions
from timonel.errors import explain
from timonel.gitops import GitError
from timonel.i18n import choose, set_language, t
from timonel.ledger import open_ledger
from timonel.router import Plan, parse_force
from timonel.stats import summary

console = Console(highlight=False, soft_wrap=True)
errors = Console(stderr=True, highlight=False, soft_wrap=True)
STATUS_STYLE = {
    "succeeded": "green",
    "applied": "green",
    "failed": "red",
    "cancelled": "yellow",
    "interrupted": "yellow",
    "running": "cyan",
    "planning": "cyan",
    "discarded": "dim",
}
VERIFY_HINTS = (
    ("pyproject.toml", ["python", "-m", "pytest", "-q"]),
    ("package.json", ["npm", "test", "--silent"]),
    ("Cargo.toml", ["cargo", "test", "--quiet"]),
    ("go.mod", ["go", "test", "./..."]),
)


def split_command(text: str) -> tuple[str, ...]:
    return tuple(shlex.split(text, posix=os.name != "nt"))


def emit_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def health_table(report: list[AgentHealth], smoke: bool) -> Table:
    table = Table(title="Timonel doctor", title_justify="left")
    for column in ("agent", "installed", "version", "auth", "billing", "flags", "models"):
        table.add_column(column)
    if smoke:
        table.add_column("smoke")
    for item in report:
        auth_style = {"ok": "green", "missing": "red", "unknown": "yellow"}[item.auth]
        row = [
            item.name,
            "[green]yes[/]" if item.installed else "[red]no[/]",
            escape(item.version or "-"),
            f"[{auth_style}]{item.auth}[/] {escape(item.auth_detail)}",
            item.billing,
            "verified" if item.verified else "[yellow]unverified[/]",
            escape(", ".join(item.models[:6]) or "-"),
        ]
        if smoke:
            mark = (
                "-"
                if item.smoke_ok is None
                else "[green]pass[/]"
                if item.smoke_ok
                else "[red]fail[/]"
            )
            row.append(f"{mark} {escape(item.smoke)}")
        table.add_row(*row)
    return table


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    if args.fix:
        return commands.doctor_fix(config, args.yes)
    report = diagnose(
        config, smoke=args.smoke, models=True, names=config.agents if args.all else None
    )
    if config.privacy.update_check and (latest := commands.check_update(__version__)):
        console.print(t("doctor.update", version=latest))
    if args.json:
        emit_json([item.__dict__ for item in report])
    else:
        console.print(health_table(report, args.smoke))
        for item in report:
            for problem in item.problems:
                console.print(f"[yellow]![/] {item.name}: {escape(problem)}")
        console.print(f"config: {config.source or 'built-in defaults'}")
    usable = [item for item in report if item.usable]
    if args.smoke:
        return 0 if all(item.smoke_ok for item in usable) and usable else 1
    return 0 if usable else 1


def cmd_models(args: argparse.Namespace, config: Config) -> int:
    report = diagnose(config, models=True)
    chosen = [item for item in report if not args.agent or item.name == args.agent]
    if args.agent and not chosen:
        errors.print(f"unknown agent {args.agent!r}")
        return 2
    if args.json:
        emit_json({item.name: list(item.models) for item in chosen})
        return 0
    table = Table(title="Models", title_justify="left")
    table.add_column("agent")
    table.add_column("models")
    for item in chosen:
        note = "" if item.installed else " [dim](not installed)[/]"
        table.add_row(
            item.name,
            escape(", ".join(item.models) or "CLI default (any model id is accepted)") + note,
        )
    console.print(table)
    return 0


def print_plan(plan: Plan) -> None:
    item = plan.classification
    console.print(
        f"[bold]classification[/] complexity={item.complexity} kind={item.kind} risk={item.risk} "
        f"write={item.needs_write} files~{item.estimated_files} [dim]({item.source}: {escape(item.reason)})[/]"
    )
    table = Table(title=f"route · profile {plan.profile}", title_justify="left")
    for column in ("#", "tier", "agent", "model", "effort", "billing"):
        table.add_column(column)
    for index, step in enumerate(plan.steps(), 1):
        table.add_row(
            str(index),
            str(step.tier),
            step.agent,
            step.model or "default",
            step.effort or "-",
            step.billing,
        )
    console.print(table)
    for reason in plan.reasons:
        console.print(f"  [cyan]•[/] {escape(reason)}")
    for skipped in plan.skipped:
        console.print(f"  [dim]- {escape(skipped)}[/]")


def make_engine(config: Config, live: bool = False) -> Engine:
    def show(run_id: str, kind: str, data: dict[str, Any]) -> None:
        if kind == "attempt.started":
            console.print(
                f"[cyan]→[/] tier {data['tier']} {data['role']}: {data['agent']}:{data['model']} ({data['billing']})"
            )
        elif kind == "attempt.finished":
            style = "green" if data["outcome"] == "succeeded" else "red"
            cost = data.get("cost_usd")
            price = (
                f" ${cost:.4f}{'~' if data.get('cost_estimated') else ''}"
                if cost is not None
                else ""
            )
            console.print(f"  [{style}]{data['outcome']}[/] in {data['duration_s']:.1f}s{price}")
        elif kind == "escalate":
            console.print(f"  [yellow]↑ tier {data['to_tier']}[/]")
        elif kind == "verify.finished":
            style = "green" if data.get("passed") else "red"
            console.print(
                f"  [{style}]verify {'passed' if data.get('passed') else 'failed'}[/]: {escape(str(data.get('command', '')))}"
            )
        elif kind == "review":
            verdict = {True: "approved", False: "rejected", None: "no verdict"}[data.get("approve")]
            issues = "; ".join(map(str, data.get("issues") or []))
            console.print(f"  [yellow]review {verdict}[/] {escape(issues)}")
        elif kind in ("cooldown", "budget", "race.winner", "decompose.plan", "hook.finished"):
            console.print(
                f"  [yellow]{kind}[/] {escape(json.dumps(data, ensure_ascii=False)[:300])}"
            )

    ledger = open_ledger(config.paths.ledger)
    engine = Engine(config, ledger, on_event=show if live else None)
    engine.confirm = lambda question: commands.confirm_yes(question)
    return engine


def cmd_plan(args: argparse.Namespace, config: Config) -> int:
    engine = make_engine(config)
    root = gitops.repo_root(Path(args.repo))
    force = parse_force(args.force) if args.force else None
    if args.model_classify:
        plan = engine.preview(args.task, profile=args.profile, force=force, cwd=root)
    else:
        plan = engine.plan(args.task, profile=args.profile, force=force, cwd=root)
    if args.json:
        emit_json(plan.to_dict())
    else:
        print_plan(plan)
    return 0


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input(f"{question} [type 'yes'] ").strip().lower() == "yes"


def cmd_run(args: argparse.Namespace, config: Config) -> int:
    if args.dangerous:
        allowed = [name for name, agent in config.agents.items() if agent.allow_dangerous]
        if not allowed:
            errors.print(
                "--dangerous has no effect: no agent sets allow_dangerous: true in routes.yaml"
            )
            return 2
        warning = f"This passes permission-bypass flags to: {', '.join(allowed)}. Continue?"
        if not confirm(warning, args.yes):
            errors.print("aborted: dangerous mode needs explicit confirmation")
            return 2
    verify = tuple(split_command(command) for command in args.verify) if args.verify else None
    options = RunOptions(
        task=args.task,
        repo=Path(args.repo),
        profile=args.profile,
        force=parse_force(args.force) if args.force else None,
        verify=verify,
        race=args.race,
        decompose=True if args.decompose else None,
        allow_dangerous=args.dangerous,
    )
    engine = make_engine(config, live=not args.json)
    run_id = engine.start(options)
    if not args.json:
        console.print(f"[bold]run {run_id}[/]")
    try:
        record = engine.run(options, run_id)
    except KeyboardInterrupt:
        errors.print(f"interrupted; resume with: timonel resume {run_id}")
        return 130
    if args.json:
        emit_json({**record, "attempts": engine.ledger.attempts(run_id)})
    else:
        print_result(record)
    return 0 if record["status"] == "succeeded" else 1


def print_result(record: dict[str, Any]) -> None:
    style = STATUS_STYLE.get(record["status"], "white")
    console.print(f"[{style}]{record['status']}[/] run {record['id']}")
    if record.get("summary"):
        console.print(escape(record["summary"][-4000:]))
    if record.get("error"):
        console.print(f"[red]{escape(record['error'][-2000:])}[/]")
    if record["status"] == "succeeded" and record.get("branch"):
        console.print(f"changes on branch [bold]{record['branch']}[/] ({record.get('diff_stat')})")
        console.print(
            f"  timonel diff {record['id']}   timonel apply {record['id']}   timonel discard {record['id']}"
        )


def cmd_status(args: argparse.Namespace, config: Config) -> int:
    engine = make_engine(config)
    if args.run_id:
        record = engine.require(args.run_id)
        attempts = engine.ledger.attempts(record["id"])
        if args.json:
            emit_json(
                {**record, "attempts": attempts, "children": engine.ledger.child_runs(record["id"])}
            )
            return 0
        print_result(record)
        table = Table(title="attempts", title_justify="left")
        for column in ("#", "role", "tier", "agent", "model", "outcome", "time", "cost"):
            table.add_column(column)
        for row in attempts:
            cost = (
                "-"
                if row["cost_usd"] is None
                else f"${row['cost_usd']:.4f}{'~' if row['cost_estimated'] else ''}"
            )
            table.add_row(
                str(row["seq"]),
                row["role"],
                str(row["tier"]),
                row["agent"],
                row["model"] or "default",
                row["outcome"],
                f"{row['duration_s'] or 0:.1f}s",
                cost,
            )
        console.print(table)
        return 0
    runs = engine.ledger.list_runs(limit=args.limit)
    if args.json:
        emit_json(runs)
        return 0
    table = Table(title="recent runs", title_justify="left")
    for column in ("id", "status", "profile", "task", "changes"):
        table.add_column(column)
    for run in runs:
        style = STATUS_STYLE.get(run["status"], "white")
        table.add_row(
            run["id"],
            f"[{style}]{run['status']}[/]",
            run["profile"],
            escape(run["task"].splitlines()[0][:60]),
            run.get("diff_stat") or "",
        )
    console.print(table)
    return 0


def cmd_diff(args: argparse.Namespace, config: Config) -> int:
    text = make_engine(config).diff(args.run_id)
    sys.stdout.write(text or "no pending diff\n")
    return 0


def cmd_apply(args: argparse.Namespace, config: Config) -> int:
    record = make_engine(config).apply(args.run_id)
    console.print(f"[green]applied[/] run {record['id']} into {record['repo']}")
    return 0


def cmd_discard(args: argparse.Namespace, config: Config) -> int:
    record = make_engine(config).discard(args.run_id)
    console.print(f"discarded run {record['id']}")
    return 0


def cmd_resume(args: argparse.Namespace, config: Config) -> int:
    engine = make_engine(config, live=True)
    try:
        record = engine.resume(engine.require(args.run_id)["id"], allow_dangerous=False)
    except KeyboardInterrupt:
        return 130
    print_result(record)
    return 0 if record.get("status") == "succeeded" else 1


def cmd_stats(args: argparse.Namespace, config: Config) -> int:
    data = summary(config, open_ledger(config.paths.ledger))
    if args.json:
        emit_json(data)
        return 0
    for title, key in (
        ("by agent", "by_agent"),
        ("by tier", "by_tier"),
        ("by task kind", "by_kind"),
    ):
        table = Table(title=title, title_justify="left")
        for column in ("", "attempts", "success", "cost", "mean time"):
            table.add_column(column)
        for group in data[key]:
            duration = group["mean_duration_s"]
            table.add_row(
                group["key"],
                str(group["attempts"]),
                f"{group['success_rate']:.0%}",
                f"${group['cost_usd']:.4f} ({group['cost_known']} known)",
                "-" if duration is None else f"{duration:.1f}s",
            )
        console.print(table)
    saving = data["savings"]
    console.print(f"spend today: ${data['spend_today_usd']:.4f}")
    if saving["saved_usd"] is None:
        console.print(f"savings: n/a ({saving['baseline_source']})")
    else:
        console.print(
            f"savings: ${saving['saved_usd']:.4f} over {saving['runs']} runs "
            f"(actual ${saving['actual_usd']:.4f} vs always-top-tier ${saving['baseline_usd']:.4f}; {saving['baseline_source']})"
        )
    return 0


def suggest_verify(repo: Path) -> list[str] | None:
    for marker, command in VERIFY_HINTS:
        if (repo / marker).is_file():
            return command
    return None


def cmd_init(args: argparse.Namespace, config: Config) -> int:
    target = Path(args.output) if args.output else user_config_path()
    if target.exists() and not args.force:
        errors.print(f"{target} exists; use --force to overwrite")
        return 2
    report = diagnose(config, models=False)
    usable = {item.name for item in report if item.usable}
    console.print(health_table(report, smoke=False))
    if not usable:
        errors.print("no usable agent found; install and log in to at least one CLI")
        return 1
    tiers = {
        tier: [
            {"agent": rung.agent, "model": rung.model, "effort": rung.effort}
            for rung in rungs
            if rung.agent in usable
        ]
        for tier, rungs in config.tiers.items()
    }
    document: dict[str, Any] = {
        "version": 1,
        "tiers": {tier: rungs for tier, rungs in tiers.items() if rungs},
    }
    try:
        repo = gitops.repo_root(Path.cwd())
    except GitError:
        repo = None
    hint = suggest_verify(repo) if repo else None
    verify: list[list[str]] = []
    if hint and (args.yes or confirm(f"Use '{' '.join(hint)}' to verify changes?", False)):
        verify.append(hint)
    document["verification"] = {"commands": verify}
    text = (
        "# Generated by timonel init. Fields omitted here inherit the built-in defaults.\n"
        + yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    )
    validate_text(text, str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    console.print(f"[green]wrote[/] {target}")
    return 0


def cmd_validate(args: argparse.Namespace, config: Config) -> int:
    target = Path(args.file) if args.file else config.source
    if target is None:
        console.print("using built-in defaults: valid")
        return 0
    validate_text(target.read_text(encoding="utf-8"), str(target))
    console.print(f"[green]valid[/] {target}")
    return 0


def cmd_ui(args: argparse.Namespace, config: Config) -> int:
    from timonel.web.server import serve

    return serve(
        config,
        port=config.ui.port if args.port is None else args.port,
        open_browser=config.ui.open_browser and not args.no_browser,
        repo=Path(args.repo),
    )


def cmd_mcp(args: argparse.Namespace, config: Config) -> int:
    from timonel.mcp import serve_stdio

    return serve_stdio(config)


ADVANCED_FLAGS = {"--race", "--decompose", "--dangerous", "--model-classify", "--set", "--config"}
EXPLAINED = {
    "run": "explain.run",
    "plan": "explain.plan",
    "apply": "explain.apply",
    "discard": "explain.discard",
    "start": "explain.start",
}


def hide_advanced(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if any(flag in ADVANCED_FLAGS for flag in action.option_strings):
            action.help = argparse.SUPPRESS
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                hide_advanced(child)


def build_parser(simple: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timonel", description="Route coding tasks to the right CLI agent and model."
    )
    parser.add_argument("--version", action="version", version=f"timonel {__version__}")
    parser.add_argument("--config", help="routes.yaml to use (default: user config dir)")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override one option for this command",
    )
    parser.add_argument(
        "--explain", action="store_true", help="show what the command will do and why, then ask"
    )
    parser.add_argument("--lang", choices=("en", "es", "pt"), help="language of messages")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser(
        "start", help="guided setup: install, log in and configure what you need"
    )
    start.add_argument("--agents", help="comma-separated agents to set up")
    start.add_argument(
        "--dry-run", action="store_true", help="show every step without changing anything"
    )
    start.add_argument(
        "--yes", action="store_true", help="non-interactive; only runs actions listed in --allow"
    )
    start.add_argument(
        "--allow",
        action="append",
        metavar="COMPONENT",
        help="component allowed with --yes (repeatable)",
    )
    start.set_defaults(handler=commands.cmd_start)

    demo_parser = sub.add_parser("demo", help="try the whole flow with simulated agents")
    demo_parser.add_argument(
        "--path", help="folder for the demo project (default: a temporary folder)"
    )
    demo_parser.set_defaults(handler=commands.cmd_demo)

    learn = sub.add_parser("learn", help="short explanations of the main ideas")
    learn.add_argument("topic", nargs="?")
    learn.set_defaults(handler=commands.cmd_learn)

    doctor = sub.add_parser("doctor", help="detect installed and authenticated agents")
    doctor.add_argument("--smoke", action="store_true", help="run a tiny real prompt per agent")
    doctor.add_argument("--fix", action="store_true", help="repair problems step by step")
    doctor.add_argument("--all", action="store_true", help="include disabled connectors")
    doctor.add_argument("--yes", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=cmd_doctor)

    models = sub.add_parser("models", help="list models per agent")
    models.add_argument("agent", nargs="?")
    models.add_argument("--json", action="store_true")
    models.set_defaults(handler=cmd_models)

    for name, handler, help_text in (
        ("plan", cmd_plan, "show classification and route without running"),
        ("run", cmd_run, "run a task"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("task")
        command.add_argument("--repo", default=".")
        command.add_argument("--profile")
        command.add_argument("--force", metavar="AGENT[:MODEL]")
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler=handler)
        if name == "plan":
            command.add_argument(
                "--model-classify", action="store_true", help="ask the classifier model"
            )
        else:
            command.add_argument(
                "--verify",
                action="append",
                metavar="COMMAND",
                help="verification command (repeatable)",
            )
            command.add_argument(
                "--race", action="store_true", help="run two agents and keep the best result"
            )
            command.add_argument(
                "--decompose", action="store_true", help="split into subtasks first"
            )
            command.add_argument(
                "--dangerous", action="store_true", help="pass permission-bypass flags (asks first)"
            )
            command.add_argument("--yes", action="store_true", help="answer yes to confirmations")

    status = sub.add_parser("status", help="list runs or show one")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--limit", type=int, default=20)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    for name, handler, help_text in (
        ("diff", cmd_diff, "show the diff of a run"),
        ("apply", cmd_apply, "merge a run's branch into the repository"),
        ("discard", cmd_discard, "delete a run's worktree and branch"),
        ("resume", cmd_resume, "resume an interrupted run"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("run_id")
        command.set_defaults(handler=handler)

    stats = sub.add_parser("stats", help="success, cost, latency and savings")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(handler=cmd_stats)

    config_parser = sub.add_parser("config", help="list, explain and change options")
    config_parser.add_argument(
        "action",
        choices=(
            "list",
            "get",
            "set",
            "unset",
            "edit",
            "schema",
            "validate",
            "export",
            "import",
            "reset",
            "explain",
        ),
    )
    config_parser.add_argument("key", nargs="?")
    config_parser.add_argument("value", nargs="?")
    config_parser.add_argument(
        "--project", action="store_true", help="write to .timonel.yaml of the repository"
    )
    config_parser.add_argument("--repo", default=".")
    config_parser.add_argument("--area")
    config_parser.add_argument(
        "--all", action="store_true", help="include advanced options in simple mode"
    )
    config_parser.add_argument(
        "--effective", action="store_true", help="export the merged configuration"
    )
    config_parser.add_argument("--json", action="store_true")
    config_parser.set_defaults(handler=commands.cmd_config)

    profiles = sub.add_parser("profiles", help="list, create, copy, use or delete profiles")
    profiles.add_argument("action", choices=("list", "create", "copy", "use", "delete"))
    profiles.add_argument("name", nargs="?")
    profiles.add_argument("--from", dest="source")
    profiles.set_defaults(handler=commands.cmd_profiles)

    agents = sub.add_parser(
        "agents", help="list, add, remove, enable, disable, inspect or test connectors"
    )
    agents.add_argument(
        "action", choices=("list", "add", "remove", "info", "test", "enable", "disable")
    )
    agents.add_argument(
        "name",
        nargs="?",
        help="agent name, or a connector .yaml for add (omit for the guided custom connector)",
    )
    agents.set_defaults(handler=commands.cmd_agents)

    trust = sub.add_parser(
        "trust", help="trust the .timonel.yaml of a repository after reviewing it"
    )
    trust.add_argument("--repo", default=".")
    trust.add_argument("--revoke", action="store_true")
    trust.add_argument("--yes", action="store_true")
    trust.set_defaults(handler=commands.cmd_trust)

    keys_parser = sub.add_parser("keys", help="store API keys in the system keyring")
    keys_parser.add_argument("action", choices=("list", "set", "delete"))
    keys_parser.add_argument("name", nargs="?")
    keys_parser.set_defaults(handler=commands.cmd_keys)

    mode = sub.add_parser("mode", help="show or switch between simple and expert mode")
    mode.add_argument("mode", nargs="?", choices=("simple", "expert"))
    mode.set_defaults(handler=commands.cmd_mode)

    purge = sub.add_parser("purge", help="delete old runs from the local ledger")
    purge.add_argument("--older-than", type=int, metavar="DAYS")
    purge.add_argument("--all", action="store_true")
    purge.set_defaults(handler=commands.cmd_purge)

    init = sub.add_parser("init", help="generate routes.yaml from what doctor detects")
    init.add_argument("--output")
    init.add_argument("--force", action="store_true")
    init.add_argument("--yes", action="store_true")
    init.set_defaults(handler=cmd_init)

    validate = sub.add_parser("validate", help="validate a routes.yaml")
    validate.add_argument("file", nargs="?")
    validate.set_defaults(handler=cmd_validate)

    ui = sub.add_parser("ui", help="open the local web interface")
    ui.add_argument("--port", type=int)
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--repo", default=".")
    ui.set_defaults(handler=cmd_ui)

    mcp = sub.add_parser("mcp", help="serve plan/run/status over MCP (stdio)")
    mcp.set_defaults(handler=cmd_mcp)
    if simple:
        hide_advanced(parser)
    return parser


def current_repo(path: str) -> Path | None:
    try:
        return gitops.repo_root(Path(path))
    except GitError:
        return None


def report_error(error: BaseException) -> None:
    detail = explain(error)
    errors.print(f"[red]{detail.code}[/] {escape(detail.title)}")
    problems = getattr(error, "problems", None)
    for line in problems or [detail.detail]:
        errors.print(f"  {escape(str(line))}")
    errors.print(f"  [bold]{t('common.fix')}:[/] {escape(detail.fix)}")
    errors.print(f"  [dim]{detail.link}[/]")


def explain_first(args: argparse.Namespace) -> bool:
    errors.print(f"[bold]{t('explain.header')}[/]")
    key = EXPLAINED.get(args.command)
    errors.print(t(key) if key else t("explain.generic", command=f"timonel {args.command}"))
    if not sys.stdin.isatty():
        errors.print(t("explain.not_interactive"))
        return False
    return commands.confirm_yes(t("common.continue"))


def simple_mode_requested(argv: Sequence[str] | None) -> bool:
    try:
        return load_config().mode == "simple"
    except Exception:
        return True


def main(argv: Sequence[str] | None = None) -> int:
    os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    arguments = list(sys.argv[1:] if argv is None else argv)
    simple = "-h" in arguments or "--help" in arguments
    args = build_parser(simple and simple_mode_requested(arguments)).parse_args(arguments)
    set_language(args.lang or choose("auto"))
    try:
        config = load_config(
            Path(args.config) if args.config else None,
            repo=current_repo(getattr(args, "repo", ".")),
            overrides=args.set,
        )
        set_language(args.lang or choose(config.language))
        if config.paths.data_dir:
            os.environ["TIMONEL_DATA_DIR"] = config.paths.data_dir
        if (
            config.project.path is not None
            and not config.project.trusted
            and args.command not in ("trust", "config")
        ):
            errors.print(f"[yellow]{escape(t('trust.warning', path=config.project.path))}[/]")
        if args.explain and not explain_first(args):
            return 0
        return int(args.handler(args, config))
    except KeyboardInterrupt:
        errors.print(t("common.aborted"))
        return 130
    except ConfigError as error:
        report_error(error)
        return 2
    except Exception as error:
        report_error(error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
