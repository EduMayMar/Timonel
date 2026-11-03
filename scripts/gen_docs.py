from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from timonel import schema

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "src" / "timonel" / "agents"
DOCS = ROOT / "docs"

TEXT = {
    "en": {
        "title": "Configuration reference",
        "intro": (
            "Generated from `src/timonel/schema/config.schema.yaml` by `scripts/gen_docs.py`; "
            "do not edit by hand. Values are merged in this order: built-in defaults, your user file, "
            "a trusted project `.timonel.yaml`, `TIMONEL_SECTION__KEY` environment variables and "
            "`--set key=value`. `timonel config explain <key>` shows where a value comes from."
        ),
        "columns": "| Key | Type | Default | Meaning |",
        "advanced": "advanced",
    },
    "es": {
        "title": "Referencia de configuración",
        "intro": (
            "Generado desde `src/timonel/schema/config.schema.yaml` con `scripts/gen_docs.py`; "
            "no lo edites a mano. Los valores se combinan en este orden: valores por defecto, tu "
            "archivo de usuario, el `.timonel.yaml` de un proyecto de confianza, las variables "
            "`TIMONEL_SECCION__CLAVE` y `--set clave=valor`. `timonel config explain <clave>` "
            "muestra de dónde sale cada valor."
        ),
        "columns": "| Clave | Tipo | Por defecto | Significado |",
        "advanced": "avanzada",
    },
    "pt": {
        "title": "Referência de configuração",
        "intro": (
            "Gerado a partir de `src/timonel/schema/config.schema.yaml` por `scripts/gen_docs.py`; "
            "não edite à mão. Os valores são combinados nesta ordem: padrões embutidos, seu arquivo "
            "de usuário, o `.timonel.yaml` de um projeto confiável, variáveis `TIMONEL_SECAO__CHAVE` "
            "e `--set chave=valor`. `timonel config explain <chave>` mostra a origem de cada valor."
        ),
        "columns": "| Chave | Tipo | Padrão | Significado |",
        "advanced": "avançada",
    },
}


def area_title(language: str, area: str) -> str:
    catalog = json.loads(
        (ROOT / "src" / "timonel" / "locales" / f"{language}.json").read_text(encoding="utf-8")
    )
    return str(catalog.get(f"ui.settings.area.{area}", area))


def cell(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text.replace("|", "\\|").replace("\n", " ")


def type_label(node: dict[str, Any]) -> str:
    if node.get("enum"):
        return " \\| ".join(f"`{item}`" for item in node["enum"])
    kind = node.get("type", "object")
    if kind == "array":
        return f"list of {(node.get('items') or {}).get('type', 'object')}"
    return str(kind)


def configuration(language: str) -> str:
    text = TEXT[language]
    areas = schema.load_schema().get("x-areas", [])
    options = schema.options()
    lines = [f"# {text['title']}", "", text["intro"], ""]
    for area in areas:
        chosen = [option for option in options if option.area == area]
        if not chosen:
            continue
        lines += [f"## {area_title(language, area)}", "", text["columns"], "|---|---|---|---|"]
        for option in chosen:
            default = f"`{cell(option.node['default'])}`" if "default" in option.node else "—"
            flag = f" *({text['advanced']})*" if option.advanced else ""
            lines.append(
                f"| `{option.key}` | {type_label(option.node)} | "
                f"{default} | {cell(option.description(language))}{flag} |"
            )
        lines.append("")
    return "\n".join(lines)


def connectors() -> str:
    lines = [
        "# Connectors",
        "",
        "Generated from `src/timonel/agents/*.yaml` by `scripts/gen_docs.py`; do not edit by hand.",
        "",
        "**verified**: every flag was read in the CLI's own `--help` at the version shown. "
        "**docs**: taken from official documentation only. "
        "**unverified**: community-provided, confirm with `timonel agents test <name>` first.",
        "",
        "| Name | Kind | Title | Vendor | Status | Version | Checked | Enabled by default | Install |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for path in sorted(AGENTS.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        verification = data.get("verification") or {}
        install = data.get("install") or {}
        how = next(
            (
                " ".join(install[key])
                for key in ("npm", "pipx", "uv", "brew", "winget")
                if install.get(key)
            ),
            "script" if install.get("script") else "—",
        )
        enabled = (data.get("agent") or {}).get("enabled", True)
        lines.append(
            f"| `{data['name']}` | {data.get('kind', 'agent')} | {cell(data.get('title', ''))} | "
            f"{cell(data.get('vendor', ''))} | {verification.get('status', 'unverified')} | "
            f"{verification.get('version', '—')} | {verification.get('date', '—')} | "
            f"{'yes' if enabled else 'no'} | `{cell(how)}` |"
        )
    lines.append("")
    return "\n".join(lines)


def outputs() -> dict[Path, str]:
    found = {DOCS / "connectors.md": connectors()}
    for language in TEXT:
        suffix = "" if language == "en" else f".{language}"
        found[DOCS / "reference" / f"configuration{suffix}.md"] = configuration(language)
    return found


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    stale = []
    for path, content in outputs().items():
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for name in stale:
        print(f"out of date: {name} (run python scripts/gen_docs.py)")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
