from __future__ import annotations

import json
import locale
import os
import re
from collections.abc import Mapping
from functools import cache
from importlib import resources

LANGUAGES = ("en", "es", "pt")
PARAM_RE = re.compile(r"\{([a-z_]+)\}")
state = {"language": "en"}


@cache
def catalog(language: str) -> dict[str, str]:
    text = (
        resources.files("timonel.locales").joinpath(f"{language}.json").read_text(encoding="utf-8")
    )
    loaded: dict[str, str] = json.loads(text)
    return loaded


def system_language(environ: Mapping[str, str]) -> str:
    for name in ("TIMONEL_LANG", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = environ.get(name, "")
        if value[:2].lower() in LANGUAGES:
            return value[:2].lower()
    try:
        current = locale.getlocale()[0] or ""
    except ValueError:
        current = ""
    lowered = current.lower()
    for language, names in (("es", ("es", "spanish")), ("pt", ("pt", "portuguese"))):
        if lowered.startswith(names):
            return language
    return "en"


def choose(configured: str = "auto", environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    forced = source.get("TIMONEL_LANG", "")[:2].lower()
    if forced in LANGUAGES:
        return forced
    if configured in LANGUAGES:
        return configured
    return system_language(source)


def set_language(language: str) -> None:
    state["language"] = language if language in LANGUAGES else "en"


def current() -> str:
    return state["language"]


def t(message_key: str, /, **params: object) -> str:
    text = catalog(current()).get(message_key) or catalog("en").get(message_key) or message_key
    return PARAM_RE.sub(lambda match: str(params.get(match.group(1), match.group(0))), text)


def keys_with_prefix(prefix: str, language: str = "en") -> list[str]:
    return sorted(key for key in catalog(language) if key.startswith(prefix))
