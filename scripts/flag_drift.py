from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ARG_KEYS = (
    "base_args",
    "read_args",
    "write_args",
    "model_args",
    "effort_args",
    "prompt_args",
    "dangerous_args",
)
FLAG = re.compile(r"^(--?[A-Za-z][A-Za-z0-9-]*)")


def connector_flags(agent: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ARG_KEYS:
        for token in agent.get(key) or []:
            match = FLAG.match(str(token))
            if match:
                found.add(match.group(1))
    return found


def help_text(command: str, variants: list[list[str]], timeout: int) -> str:
    executable = shutil.which(command)
    if executable is None:
        raise FileNotFoundError(command)
    chunks = []
    for argv in variants:
        result = subprocess.run(
            [executable, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        chunks.append(result.stdout + result.stderr)
    return "\n".join(chunks)


def missing_flags(flags: set[str], text: str) -> list[str]:
    return sorted(
        flag for flag in flags if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text)
    )


def check(folder: Path, timeout: int) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        drift = (data.get("install") or {}).get("drift")
        agent = data.get("agent")
        if not drift or not agent:
            continue
        try:
            text = help_text(agent["command"], drift.get("help") or [["--help"]], timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            report[data["name"]] = {"error": f"{type(error).__name__}: {error}"}
            continue
        report[data["name"]] = {"missing": missing_flags(connector_flags(agent), text)}
    return report


def markdown(report: dict[str, Any]) -> str:
    lines = ["Nightly flag check against each CLI's own `--help`.", ""]
    for name, entry in report.items():
        if entry.get("error"):
            lines.append(f"- **{name}**: could not run ({entry['error']})")
        elif entry["missing"]:
            flags = ", ".join(f"`{flag}`" for flag in entry["missing"])
            lines.append(f"- **{name}**: flags not found in help: {flags}")
    lines += [
        "",
        "Update `src/timonel/agents/<name>.yaml` (and its `verification` block) or confirm the "
        "flag still exists. See docs/adding-an-agent.md.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", type=Path, default=Path("src/timonel/agents"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    report = check(args.agents, args.timeout)
    print(json.dumps(report, indent=2))
    problems = {name: entry for name, entry in report.items() if entry.get("missing")}
    if args.markdown and problems:
        args.markdown.write_text(markdown(problems), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
