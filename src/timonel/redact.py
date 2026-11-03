from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

MASK = "[REDACTED]"
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE)
PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"sk-ant-[A-Za-z0-9_\-]{10,}",
        r"sk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_\-]{30,}",
        r"xox[abprs]-[A-Za-z0-9\-]{10,}",
        r"(?i)bearer\s+[A-Za-z0-9._\-~+/]{16,}=*",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        r"(?i)(?<![A-Za-z0-9])(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{12,}",
    )
)


def secret_values(environ: Mapping[str, str] | None = None, extra: Iterable[str] = ()) -> list[str]:
    source = os.environ if environ is None else environ
    values = {
        value for name, value in source.items() if SECRET_NAME.search(name) and len(value) >= 8
    }
    values.update(value for value in extra if len(value) >= 8)
    return sorted(values, key=len, reverse=True)


class Redactor:
    def __init__(self, environ: Mapping[str, str] | None = None, extra: Iterable[str] = ()) -> None:
        self.values = secret_values(environ, extra)

    def __call__(self, text: str) -> str:
        for value in self.values:
            text = text.replace(value, MASK)
        for pattern in PATTERNS:
            text = pattern.sub(MASK, text)
        return text


def redact(text: str) -> str:
    return Redactor()(text)
