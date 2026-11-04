from __future__ import annotations

import argparse
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "packaging"
ASSETS = {
    "sha_windows": "timonel-windows-x64.exe",
    "sha_macos": "timonel-macos-arm64",
    "sha_linux": "timonel-linux-x64",
}


def read_sums(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            found[parts[1].lstrip("*")] = parts[0]
    return found


def fill(version: str, sums: dict[str, str], out: Path) -> list[Path]:
    values = {"version": version}
    for key, asset in ASSETS.items():
        if asset not in sums:
            raise SystemExit(f"{asset} is missing from the checksum file")
        values[key] = sums[asset]
    written = []
    for template in sorted(TEMPLATES.rglob("*")):
        if not template.is_file() or template.name == "README.md":
            continue
        target = out / template.relative_to(TEMPLATES)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            Template(template.read_text(encoding="utf-8")).substitute(values), encoding="utf-8"
        )
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("sums", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "manifests")
    args = parser.parse_args(argv)
    for path in fill(args.version, read_sums(args.sums.read_text(encoding="utf-8")), args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
