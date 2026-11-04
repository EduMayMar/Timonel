from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import pty
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fakes" / "fake_agent.py"
AGENTS = ("claude", "codex", "copilot", "agy")
ROUTES = "tui: {check_timeout_s: 10}\nbudgets:\n  - {period: day, max_usd: 5}\n"
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="css/xterm.css">
<style>body{margin:0;background:#0d1726}#t{padding:14px;display:inline-block;background:#0d1726}</style>
</head><body><div id="t"></div><script src="lib/xterm.js"></script><script>
const term = new Terminal({cols: COLS, rows: ROWS, fontFamily: "DejaVu Sans Mono, monospace", fontSize: 15,
  lineHeight: 1.0, theme: {background: "#0d1726", foreground: "#e8e2d0", cursor: "#e8e2d0"}, allowTransparency: false});
term.open(document.getElementById("t"));
const bytes = Uint8Array.from(atob("DATA"), c => c.charCodeAt(0));
term.write(bytes, () => { window.done = true; });
</script></body></html>"""


def workspace(base: Path) -> tuple[Path, dict[str, str]]:
    home = base / "home"
    home.mkdir()
    (home / "routes.yaml").write_text(ROUTES, encoding="utf-8")
    bin_dir = base / "bin"
    bin_dir.mkdir()
    for name in AGENTS:
        shim = bin_dir / name
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" {name} "$@"\n', encoding="utf-8"
        )
        shim.chmod(0o755)
    project = base / "shop-api"
    project.mkdir()
    (project / "app.py").write_text("print('shop')\n", encoding="utf-8")
    (project / ".gitignore").write_text(".env\n", encoding="utf-8")
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=demo",
            "-c",
            "user.email=demo@example.com",
            "commit",
            "-q",
            "-m",
            "init",
        ],
    ):
        subprocess.run(argv, cwd=project, check=True)
    (project / "app.py").write_text("print('shop api')\n", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{Path(shutil.which('git') or '/usr/bin/git').parent}{os.pathsep}/usr/bin:/bin",
        "HOME": str(base),
        "TIMONEL_HOME": str(home),
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "PYTHONUTF8": "1",
        "LANG": "C.UTF-8",
        "FAKE_STATE_DIR": str(base),
    }
    return project, env


def record(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    cols: int,
    rows: int,
    steps: list[tuple[float, str]],
) -> bytes:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    output = b""

    def pump(seconds: float) -> None:
        nonlocal output
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    return
                output += chunk
                for _ in range(chunk.count(b"\x1b[6n")):
                    row = min(output.count(b"\n") + 1, rows)
                    os.write(fd, f"\x1b[{row};1R".encode())

    snapshot = b""
    for delay, keys in steps:
        pump(delay)
        if keys == "<snap>":
            snapshot = output
            continue
        os.write(fd, keys.encode())
    pump(1.5)
    try:
        os.kill(pid, 9)
    finally:
        os.waitpid(pid, 0)
    return snapshot or output


def shoot(xterm: Path, data: bytes, cols: int, rows: int, target: Path) -> None:
    from playwright.sync_api import sync_playwright

    page_file = xterm / "screen.html"
    page_file.write_text(
        PAGE.replace("COLS", str(cols))
        .replace("ROWS", str(rows))
        .replace("DATA", base64.b64encode(data).decode()),
        encoding="utf-8",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("TIMONEL_CHROMIUM") or None
        )
        page: Any = browser.new_page(
            device_scale_factor=2, viewport={"width": 1800, "height": 1200}
        )
        page.goto(page_file.as_uri())
        page.wait_for_function("window.done === true")
        page.wait_for_timeout(300)
        page.locator("#t").screenshot(path=str(target))
        browser.close()


SCENES: dict[str, dict[str, Any]] = {
    "terminal-home": {"cols": 100, "rows": 30, "lang": "en", "steps": [(5.0, "<snap>")]},
    "terminal-home-es-80": {"cols": 80, "rows": 32, "lang": "es", "steps": [(5.0, "<snap>")]},
    "terminal-home-60": {"cols": 60, "rows": 34, "lang": "en", "steps": [(5.0, "<snap>")]},
    "terminal-plan": {
        "cols": 100,
        "rows": 30,
        "lang": "en",
        "steps": [(5.0, "Add input validation to the signup form\r"), (4.0, "<snap>")],
    },
    "terminal-slash": {
        "cols": 100,
        "rows": 30,
        "lang": "en",
        "steps": [(5.0, "/"), (1.5, "<snap>")],
    },
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--xterm", type=Path, required=True, help="folder of the @xterm/xterm npm package"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots")
    parser.add_argument("--only", action="append")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, scene in SCENES.items():
        if args.only and name not in args.only:
            continue
        with tempfile.TemporaryDirectory(prefix="timonel-shot-") as folder:
            project, env = workspace(Path(folder))
            env["TIMONEL_LANG"] = scene["lang"]
            data = record(
                [sys.executable, "-m", "timonel"],
                project,
                env,
                scene["cols"],
                scene["rows"],
                scene["steps"],
            )
            shoot(args.xterm, data, scene["cols"], scene["rows"], args.out / f"{name}.png")
            print(json.dumps({"scene": name, "bytes": len(data)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
