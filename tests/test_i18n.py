import json
import re
from pathlib import Path

import pytest

from timonel import i18n
from timonel.errors import TimonelError, all_codes, code_for, explain

LOCALES = Path(__file__).parent.parent / "src" / "timonel" / "locales"
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def load(language: str) -> dict[str, str]:
    data: dict[str, str] = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("language", ["es", "pt"])
def test_every_language_has_every_key(language: str) -> None:
    english = load("en")
    other = load(language)
    assert sorted(set(english) - set(other)) == []
    assert sorted(set(other) - set(english)) == []


@pytest.mark.parametrize("language", ["en", "es", "pt"])
def test_placeholders_match_english_and_nothing_is_empty(language: str) -> None:
    english = load("en")
    for key, text in load(language).items():
        assert text.strip(), (language, key)
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(english[key])), (
            language,
            key,
        )


def test_every_locale_file_on_disk_is_listed() -> None:
    assert sorted(path.stem for path in LOCALES.glob("*.json")) == sorted(i18n.LANGUAGES)


def test_language_choice_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    assert i18n.choose("auto", {"TIMONEL_LANG": "pt_BR"}) == "pt"
    assert i18n.choose("es", {}) == "es"
    assert i18n.choose("auto", {"LANG": "es_ES.UTF-8"}) == "es"
    assert i18n.choose("auto", {"LANG": "C"}) in ("en", "es", "pt")
    i18n.set_language("es")
    assert i18n.t("wizard.installed", name="Git") == "Git está instalado."
    assert i18n.t("no.such.key") == "no.such.key"
    i18n.set_language("xx")
    assert i18n.current() == "en"


def test_error_codes_are_documented_and_stable() -> None:
    codes = all_codes()
    assert codes == sorted(set(codes))
    assert all(re.fullmatch(r"TMN-\d{4}", code) for code in codes)
    docs = (Path(__file__).parent.parent / "docs" / "errors.md").read_text(encoding="utf-8")
    for code in codes:
        assert f"## {code}" in docs, code


def test_errors_map_to_codes() -> None:
    from timonel.config import ConfigError
    from timonel.engine import RunError
    from timonel.gitops import GitError
    from timonel.router import RoutingError

    assert code_for(GitError("/x is not inside a git repository")) == "TMN-2001"
    assert code_for(GitError("the repository has uncommitted changes")) == "TMN-2002"
    assert code_for(RoutingError("no usable agent for this task")) == "TMN-3001"
    assert code_for(RoutingError("unknown profile 'x'")) == "TMN-3002"
    assert code_for(RunError("unknown run x")) == "TMN-4001"
    assert code_for(ConfigError(["bad"])) == "TMN-1001"
    assert code_for(ValueError("boom")) == "TMN-9001"
    detail = explain(TimonelError("TMN-8004", "sudo needed"))
    assert detail.link.endswith("#tmn-8004")
    assert detail.detail == "sudo needed"
