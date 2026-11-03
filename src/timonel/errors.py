from __future__ import annotations

from dataclasses import dataclass

from timonel.i18n import keys_with_prefix, t

DOCS_URL = "https://edumaymar.github.io/Timonel/errors/"
PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("ConfigError", "unknown key", "TMN-1002"),
    ("ConfigError", "", "TMN-1001"),
    ("GitError", "not inside a git repository", "TMN-2001"),
    ("GitError", "uncommitted changes", "TMN-2002"),
    ("GitError", "", "TMN-2099"),
    ("RoutingError", "unknown profile", "TMN-3002"),
    ("RoutingError", "unknown agent", "TMN-3003"),
    ("RoutingError", "cannot run read-only", "TMN-3004"),
    ("RoutingError", "", "TMN-3001"),
    ("ClassificationFailed", "", "TMN-3005"),
    ("RunError", "unknown run", "TMN-4001"),
    ("RunError", "can be applied", "TMN-4002"),
    ("RunError", "", "TMN-4003"),
    ("LaunchError", "metacharacters", "TMN-5002"),
    ("LaunchError", "", "TMN-5001"),
    ("SecretMissingError", "", "TMN-5003"),
    ("BudgetExceeded", "", "TMN-7001"),
    ("NetworkUnavailable", "", "TMN-8001"),
    ("NoPackageManager", "", "TMN-8002"),
    ("InstallFailed", "", "TMN-8003"),
    ("KeyringUnavailable", "", "TMN-8010"),
)


class TimonelError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Explained:
    code: str
    title: str
    detail: str
    fix: str
    link: str


def code_for(error: BaseException) -> str:
    if isinstance(error, TimonelError):
        return error.code
    names = {cls.__name__ for cls in type(error).__mro__}
    message = str(error).lower()
    for name, fragment, code in PATTERNS:
        if name in names and fragment in message:
            return code
    return "TMN-9001"


def explain(error: BaseException) -> Explained:
    code = code_for(error)
    detail = error.detail if isinstance(error, TimonelError) else str(error)
    return Explained(
        code=code,
        title=t(f"error.{code}.title"),
        detail=detail,
        fix=t(f"error.{code}.fix"),
        link=f"{DOCS_URL}#{code.lower()}",
    )


def all_codes() -> list[str]:
    return sorted({key.split(".")[1] for key in keys_with_prefix("error.")})
