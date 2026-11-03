from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from timonel import demo, gitops, keys, schema, wizard
from timonel.adapters import strip_controls
from timonel.config import (
    Config,
    ConfigError,
    get_path,
    load_config,
    parse_yaml,
    project_commands,
    project_file,
    project_problems,
    revoke_project,
    set_path,
    trust_project,
    unset_path,
    user_agents_dir,
    user_config_path,
    validate_text,
)
from timonel.doctor import diagnose, inspect_agent
from timonel.engine import RunOptions
from timonel.i18n import t
from timonel.ledger import open_ledger

console = Console(highlight=False, soft_wrap=True)
errors = Console(stderr=True, highlight=False, soft_wrap=True)
LEARN_TOPICS = (
    "agent",
    "provider",
    "worktree",
    "escalation",
    "complexity",
    "profile",
    "budget",
    "review",
    "trust",
    "ledger",
    "privacy",
)


def emit_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def ask(question: str) -> str:
    if not sys.stdin.isatty():
        return ""
    return input(question)


def confirm_yes(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    return wizard.yes(ask(f"{question} [y/N] "))


def read_user(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data, _ = parse_yaml(path.read_text(encoding="utf-8"), str(path))
    return data


def write_user(path: Path, data: dict[str, Any]) -> None:
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    validate_text(text, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.with_suffix(path.suffix + ".bak").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    path.write_text(text, encoding="utf-8")


def target_file(args: argparse.Namespace, config: Config) -> Path:
    if getattr(args, "project", False):
        root = gitops.repo_root(Path(getattr(args, "repo", ".")))
        return root / ".timonel.yaml"
    return Path(args.config) if getattr(args, "config", None) else user_config_path()


def known_key(key: str) -> list[str]:
    path = key.split(".")
    if schema.node_at(tuple(path)) is None:
        raise ConfigError(
            [
                f"unknown key {key!r}{schema.suggestion(key, [option.key for option in schema.options()])}"
            ]
        )
    return path


def cmd_learn(args: argparse.Namespace, config: Config) -> int:
    if not args.topic:
        console.print(f"[bold]{t('learn.list')}[/]")
        for topic in LEARN_TOPICS:
            console.print(f"  [cyan]{topic}[/] - {t(f'learn.{topic}.title')}")
        return 0
    if args.topic not in LEARN_TOPICS:
        errors.print(t("learn.unknown", topic=args.topic))
        return 2
    console.print(f"[bold]{t(f'learn.{args.topic}.title')}[/]")
    console.print(t(f"learn.{args.topic}.body"))
    return 0


def cmd_start(args: argparse.Namespace, config: Config) -> int:
    session = wizard.interactive_session(dict(os.environ), args.dry_run, args.yes, args.allow or [])
    installed_agents = [
        name
        for name, agent in config.agents.items()
        if agent.kind == "agent" and shutil.which(agent.command)
    ]
    default = installed_agents or ["copilot"]
    wanted = [name.strip() for name in (args.agents or "").split(",") if name.strip()]
    if not wanted and not args.yes:
        answer = ask(t("wizard.agents_question", default=", ".join(default)) + " ")
        wanted = [name.strip() for name in answer.split(",") if name.strip()]
    chosen = wanted or default
    unknown = [name for name in chosen if name not in config.agents]
    if unknown:
        errors.print(f"{t('error.TMN-3003.title')}: {', '.join(unknown)}")
        return 2
    try:
        results = wizard.run_wizard(config, session, chosen)
    except KeyboardInterrupt:
        console.print(t("common.aborted"))
        return 130
    table = Table(title=t("wizard.summary"), title_justify="left")
    table.add_column("")
    table.add_column("")
    for key, value in results.items():
        table.add_row(key, value)
    console.print(table)
    ready = [
        name
        for name in chosen
        if results.get(name) in ("ok", "installed") and config.agents[name].kind == "agent"
    ]
    to_enable = [
        name
        for name in ready
        if not config.agents[name].enabled
        or not any(rung.agent == name for rungs in config.tiers.values() for rung in rungs)
    ]
    if to_enable and not args.dry_run and (args.yes or confirm_yes(t("wizard.write_config"))):
        path = user_config_path()
        data = read_user(path)
        merged_tiers = {
            str(tier): [dict(rung) for rung in rungs]
            for tier, rungs in (data.get("tiers") or config.layers["default"]["tiers"]).items()
        }
        for name in to_enable:
            set_path(data, ["agents", name, "enabled"], True)
            for rungs in merged_tiers.values():
                if not any(rung["agent"] == name for rung in rungs):
                    rungs.append({"agent": name, "model": "", "effort": ""})
        data["tiers"] = merged_tiers
        write_user(path, data)
        console.print(t("wizard.config_written", path=path))
    return 0


def cmd_demo(args: argparse.Namespace, config: Config) -> int:
    target = Path(args.path) if args.path else Path(tempfile.mkdtemp(prefix="timonel-demo-"))
    os.environ["TIMONEL_HOME"] = str(target / ".timonel-demo")
    console.print(f"[bold]{t('demo.intro')}[/]")
    project = demo.create_project(target / "project")
    console.print(t("demo.project", path=project))
    console.print(t("demo.task", task=demo.DEMO_TASK))
    settings = demo.demo_config()
    from timonel.cli import make_engine, print_plan, print_result

    engine = make_engine(settings, live=True)
    console.print(f"\n[bold]{t('demo.step.plan')}[/]")
    plan = engine.plan(demo.DEMO_TASK, cwd=project, classification=demo.DEMO_CLASSIFICATION)
    print_plan(plan)
    console.print(f"\n[bold]{t('demo.step.run')}[/]")
    record = engine.run(RunOptions(task=demo.DEMO_TASK, repo=project, plan=plan))
    console.print(f"\n[bold]{t('demo.step.result')}[/]")
    print_result(record)
    console.print(t("demo.next", id=record["id"], path=project))
    return 0 if record["status"] == "succeeded" else 1


def flatten_values(data: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
    if isinstance(data, dict) and data and prefix not in (("tiers",), ("agents",), ("profiles",)):
        found: list[tuple[str, Any]] = []
        for key, value in data.items():
            found.extend(flatten_values(value, (*prefix, str(key))))
        return found
    return [(".".join(prefix), data)] if prefix else []


def cmd_config(args: argparse.Namespace, config: Config) -> int:
    action = args.action
    path = target_file(args, config)
    if action == "list":
        rows = []
        for option in schema.options():
            if args.area and option.area != args.area:
                continue
            if config.mode == "simple" and option.advanced and not args.all:
                continue
            try:
                value = get_path(config.raw, list(option.path))
            except KeyError:
                value = None
            origin = config.origins.get(option.key, "default")
            rows.append((option.key, value, origin))
        if args.json:
            emit_json([{"key": key, "value": value, "layer": layer} for key, value, layer in rows])
            return 0
        table = Table(title="timonel config", title_justify="left")
        for column in ("key", t("config.value"), t("config.layer")):
            table.add_column(column)
        for key, value, layer in rows:
            style = "" if layer == "default" else "[bold]"
            table.add_row(
                key, escape(json.dumps(value, ensure_ascii=False)[:80]), f"{style}{layer}"
            )
        console.print(table)
        return 0
    if action in ("get", "explain"):
        path_parts = known_key(args.key)
        value = get_path(config.raw, path_parts)
        if action == "get":
            emit_json(value)
            return 0
        node = schema.node_at(tuple(path_parts)) or {}
        option = schema.Option(tuple(path_parts), node, "", False)
        from timonel.i18n import current

        console.print(f"[bold]{args.key}[/]")
        console.print(f"  {t('config.value')}: {json.dumps(value, ensure_ascii=False)}")
        console.print(f"  {t('config.layer')}: {config.origins.get(args.key, 'default')}")
        console.print(
            f"  {t('config.default')}: {json.dumps(node.get('default'), ensure_ascii=False)}"
        )
        console.print(f"  {t('config.description')}: {option.description(current())}")
        return 0
    if action == "set":
        parts = known_key(args.key)
        data = read_user(path)
        set_path(data, parts, yaml.safe_load(args.value) if args.value.strip() else "")
        write_user(path, data)
        console.print(t("config.saved", key=args.key, path=path))
        return 0
    if action == "unset":
        parts = known_key(args.key)
        data = read_user(path)
        if not unset_path(data, parts):
            console.print(t("config.not_set", key=args.key, path=path))
            return 1
        write_user(path, data)
        console.print(t("config.removed", key=args.key, path=path))
        return 0
    if action == "edit":
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("version: 1\n", encoding="utf-8")
        editor = (
            os.environ.get("VISUAL")
            or os.environ.get("EDITOR")
            or ("notepad" if os.name == "nt" else "nano")
        )
        subprocess.run([*shlex.split(editor, posix=os.name != "nt"), str(path)], check=False)
        validate_text(path.read_text(encoding="utf-8"), str(path))
        console.print(t("config.valid"))
        return 0
    if action == "schema":
        sys.stdout.write(schema.schema_json())
        return 0
    if action == "validate":
        file = Path(args.key) if args.key else path
        if file.is_file():
            validate_text(file.read_text(encoding="utf-8"), str(file))
        console.print(t("config.valid"))
        return 0
    if action == "export":
        data = config.raw if args.effective else read_user(path)
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        if args.key:
            Path(args.key).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    if action == "import":
        source = Path(args.key)
        imported, _ = parse_yaml(source.read_text(encoding="utf-8"), str(source))
        write_user(path, imported)
        console.print(t("config.imported", source=source, path=path))
        return 0
    if action == "reset":
        if args.key:
            return cmd_config(argparse.Namespace(**{**vars(args), "action": "unset"}), config)
        if path.is_file():
            backup = path.with_suffix(path.suffix + f".{int(time.time())}.bak")
            path.replace(backup)
            console.print(t("config.reset", path=path, backup=backup))
        return 0
    raise ConfigError([f"unknown action {action!r}"])


def cmd_profiles(args: argparse.Namespace, config: Config) -> int:
    path = user_config_path()
    data = read_user(path)
    if args.action == "list":
        for name, profile in sorted(config.profiles.items()):
            mark = "*" if name == config.default_profile else " "
            console.print(
                f"{mark} {name}  start_offset={profile.start_offset} min={profile.min_tier} max={profile.max_tier}"
            )
        return 0
    if args.action in ("create", "copy"):
        source = (
            config.raw.get("profiles", {}).get(args.source or "balanced", {})
            if args.action == "copy"
            else {}
        )
        set_path(data, ["profiles", args.name], dict(source))
        write_user(path, data)
        console.print(t("profiles.created", name=args.name))
        return 0
    if args.action == "use":
        if args.name not in config.profiles:
            raise ConfigError([f"unknown profile {args.name!r}"])
        set_path(data, ["default_profile"], args.name)
        write_user(path, data)
        console.print(t("profiles.used", name=args.name))
        return 0
    unset_path(data, ["profiles", args.name])
    write_user(path, data)
    console.print(t("profiles.deleted", name=args.name))
    return 0


def cmd_trust(args: argparse.Namespace, config: Config) -> int:
    root = gitops.repo_root(Path(args.repo))
    found = project_file(root)
    if found is None:
        console.print(t("trust.nothing", path=root))
        return 1
    if args.revoke:
        revoke_project(found)
        console.print(t("trust.revoked", path=found))
        return 0
    content = found.read_bytes()
    text = content.decode("utf-8")
    data, _ = parse_yaml(text, str(found))
    validate_text(text, str(found))
    forbidden = project_problems(data, str(found))
    if forbidden:
        raise ConfigError(forbidden)
    commands = project_commands(data)
    console.print(t("trust.full", path=found))
    console.print(strip_controls(text), highlight=False, markup=False)
    console.print(t("trust.review", path=found))
    for line in commands or [t("trust.no_commands")]:
        console.print(f"  - {escape(line)}")
    if not confirm_yes(t("trust.question"), args.yes):
        console.print(t("common.aborted"))
        return 1
    trust_project(found, content)
    console.print(t("trust.done", path=found))
    return 0


def cmd_keys(args: argparse.Namespace, config: Config) -> int:
    if args.action == "set":
        import getpass

        value = getpass.getpass(t("wizard.key_prompt")).strip()
        if not value:
            console.print(t("wizard.key_empty"))
            return 1
        keys.store(args.name, value)
        console.print(t("keys.saved", name=args.name))
        return 0
    if args.action == "delete":
        keys.delete(args.name)
        console.print(t("keys.deleted", name=args.name))
        return 0
    names = sorted(
        {
            str(agent.meta.get("api_key", {}).get("env"))
            for agent in config.agents.values()
            if isinstance(agent.meta.get("api_key"), dict)
        }
        | {source for agent in config.agents.values() for source in agent.env_from.values()}
    )
    for name in names:
        where = "env" if os.environ.get(name) else "keyring" if keys.present(name) else "-"
        console.print(f"{name}: {where}")
    return 0


def cmd_mode(args: argparse.Namespace, config: Config) -> int:
    if not args.mode:
        console.print(config.mode)
        return 0
    path = user_config_path()
    data = read_user(path)
    data["mode"] = args.mode
    write_user(path, data)
    console.print(t("mode.set", mode=args.mode))
    return 0


def cmd_purge(args: argparse.Namespace, config: Config) -> int:
    days = args.older_than if args.older_than is not None else config.privacy.retention_days
    if days <= 0 and not args.all:
        console.print("retention_days is 0: use --older-than N or --all")
        return 1
    ledger = open_ledger(config.paths.ledger)
    cutoff = time.time() - days * 86400 if not args.all else float("inf")
    removed = ledger.purge(cutoff)
    console.print(t("purge.done", runs=removed, days=days if not args.all else 0))
    return 0


def connector_status(config: Config, name: str) -> str:
    return str((config.agents[name].meta.get("verification") or {}).get("status", "unverified"))


def cmd_agents(args: argparse.Namespace, config: Config) -> int:
    action = args.action
    if action == "list":
        table = Table(title="timonel agents", title_justify="left")
        for column in ("name", "kind", "enabled", "installed", "status", "title"):
            table.add_column(column)
        for name, agent in sorted(config.agents.items()):
            present = agent.kind == "provider" or shutil.which(agent.command) is not None
            table.add_row(
                name,
                agent.kind,
                "yes" if agent.enabled else "no",
                "yes" if present else "no",
                connector_status(config, name),
                escape(str(agent.meta.get("title", ""))),
            )
        console.print(table)
        return 0
    if action == "info":
        found = config.agents.get(args.name)
        if found is None:
            raise ConfigError([f"unknown agent {args.name!r}"])
        emit_json(
            {
                key: value
                for key, value in found.__dict__.items()
                if key not in ("detect", "provider")
            }
            | {
                "detect": found.detect.__dict__,
                "provider": found.provider.__dict__ if found.provider else None,
            }
        )
        return 0
    if action in ("enable", "disable"):
        if args.name not in config.agents:
            raise ConfigError([f"unknown agent {args.name!r}"])
        path = user_config_path()
        data = read_user(path)
        set_path(data, ["agents", args.name, "enabled"], action == "enable")
        write_user(path, data)
        console.print(t(f"agents.{action}d", name=args.name))
        return 0
    if action == "test":
        tested = config.agents.get(args.name)
        if tested is None:
            raise ConfigError([f"unknown agent {args.name!r}"])
        health = inspect_agent(tested, config, smoke=True)
        key = "agents.test.ok" if health.smoke_ok else "agents.test.fail"
        console.print(t(key, name=args.name, detail=health.smoke or health.auth_detail))
        return 0 if health.smoke_ok else 1
    if action == "remove":
        target = user_agents_dir() / f"{args.name}.yaml"
        if not target.is_file():
            console.print(t("agents.builtin", name=args.name))
            return 1
        target.unlink()
        console.print(t("agents.removed", name=args.name))
        return 0
    source = Path(args.name) if args.name and Path(args.name).is_file() else None
    text = source.read_text(encoding="utf-8") if source else describe_custom()
    if not text:
        return 1
    data, _ = parse_yaml(text, str(source or "custom"))
    name = str(data.get("name", ""))
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise ConfigError([f"{source or 'custom'}: name must be lowercase letters, digits, - or _"])
    folder = user_agents_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.yaml"
    previous = target.read_text(encoding="utf-8") if target.exists() else None
    target.write_text(text, encoding="utf-8")
    try:
        load_config(Path(args.config) if args.config else None)
    except ConfigError:
        if previous is None:
            target.unlink()
        else:
            target.write_text(previous, encoding="utf-8")
        raise
    console.print(t("agents.added", name=name, path=target))
    return 0


def describe_custom() -> str:
    name = ask(t("agents.custom.name") + " ").strip().lower()
    command = ask(t("agents.custom.command") + " ").strip()
    if not name or not command:
        return ""
    base = shlex.split(ask(t("agents.custom.args") + " "), posix=os.name != "nt")
    via = {"s": "stdin", "f": "file"}.get(
        ask(t("agents.custom.via") + " ").strip().lower()[:1], "arg"
    )
    flag = ask(t("agents.custom.flag") + " ").strip()
    parser = {"j": "json", "s": "stream-json", "l": "jsonl"}.get(
        ask(t("agents.custom.output") + " ").strip().lower()[:1], "text"
    )
    model = ask(t("agents.custom.model") + " ").strip()
    placeholder = {"arg": "{prompt}", "file": "{prompt_file}"}.get(via)
    prompt_args = ([flag] if flag else []) + ([placeholder] if placeholder else [])
    document = {
        "name": name,
        "kind": "agent",
        "title": name,
        "verification": {
            "status": "unverified",
            "date": time.strftime("%Y-%m-%d"),
            "source": "timonel agents add (custom)",
        },
        "agent": {
            "command": command,
            "billing": "api",
            "parser": parser,
            "prompt_via": via,
            "supports_read_only": False,
            "base_args": base,
            "prompt_args": prompt_args,
            "model_args": [model, "{model}"] if model else [],
            "detect": {"version": ["--version"]},
        },
    }
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    console.print(t("agents.custom.review"))
    console.print(escape(text))
    return text if confirm_yes(t("common.continue")) else ""


def doctor_fix(config: Config, assume_yes: bool) -> int:
    report = diagnose(config, models=False)
    problems = [
        item for item in report if not item.installed or item.auth == "missing" or item.problems
    ]
    if not problems:
        console.print(t("doctor.fix.none"))
        return 0
    console.print(t("doctor.fix.intro", count=len(problems)))
    session = wizard.interactive_session(dict(os.environ), False, assume_yes, [])
    results = wizard.run_wizard(config, session, [item.name for item in problems])
    return (
        0
        if all(
            value in ("ok", "installed", "logged_in", "key_stored") for value in results.values()
        )
        else 1
    )


def check_update(current: str) -> str | None:
    import urllib.request

    try:
        with urllib.request.urlopen("https://pypi.org/pypi/timonel/json", timeout=10) as response:
            latest = json.loads(response.read())["info"]["version"]
    except (OSError, KeyError, ValueError):
        return None
    return str(latest) if str(latest) != current else None
