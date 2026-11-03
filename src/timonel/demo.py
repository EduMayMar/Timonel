from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from timonel.classify import Classification
from timonel.config import Config, validate_text

DEMO_TASK = "Fix the bug in calc.py: add(2, 3) returns -1 instead of 5"
DEMO_CLASSIFICATION = Classification(
    1, "debug", "low", True, 1, "heuristic", "demo task: trivial one-line fix"
)
FILES = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "test_calc.py": (
        "import unittest\n\nfrom calc import add\n\n\n"
        "class AddTest(unittest.TestCase):\n"
        "    def test_adds(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
        "    def test_negative(self):\n        self.assertEqual(add(-2, 1), -1)\n"
    ),
    "README.md": "# Timonel demo\n\nA tiny project with one bug, used by `timonel demo`.\n",
}


def demo_overlay() -> str:
    python = sys.executable

    def agent(role: str) -> dict[str, object]:
        return {
            "command": python,
            "billing": "subscription",
            "parser": "text",
            "prompt_via": "stdin",
            "base_args": ["-m", "timonel.demo_agent", role],
            "detect": {"version": ["--version"]},
            "meta": {
                "title": f"Demo {role} agent",
                "vendor": "Timonel",
                "verification": {"status": "verified", "date": "simulated"},
            },
        }

    document = {
        "agents": {"demo-fast": agent("fast"), "demo-strong": agent("strong")},
        "tiers": {
            "1": [{"agent": "demo-fast", "model": "simulated-small"}],
            "2": [{"agent": "demo-strong", "model": "simulated-large"}],
        },
        "classifier": {"use_model": False},
        "review": {
            "min_tier": 2,
            "reviewers": [{"agent": "demo-fast", "model": "simulated-small"}],
        },
        "verification": {"commands": [[python, "-m", "unittest", "-q"]]},
        "budgets": [],
        "adaptive": {"enabled": False},
    }
    return json.dumps(document)


def demo_config() -> Config:
    return validate_text(demo_overlay(), "demo")


def create_project(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for name, content in FILES.items():
        (target / name).write_text(content, encoding="utf-8")
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=Timonel demo",
            "-c",
            "user.email=demo@localhost",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "Demo project",
        ],
    ):
        subprocess.run(argv, cwd=target, check=True, capture_output=True)
    return target
