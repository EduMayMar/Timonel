from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from timonel.config import Heuristics

Kind = Literal["explain", "edit", "debug", "refactor", "architecture", "review"]
Risk = Literal["low", "medium", "high"]
KINDS: tuple[Kind, ...] = ("explain", "edit", "debug", "refactor", "architecture", "review")
RISKS: tuple[Risk, ...] = ("low", "medium", "high")
READ_ONLY_KINDS = frozenset({"explain", "review"})
KEYWORD_KINDS: tuple[Kind, ...] = ("review", "architecture", "debug", "refactor", "explain")

FILE_RE = re.compile(r"(?<![\w/\\.-])((?:[\w.-]+[/\\])*[\w-]+\.[A-Za-z][A-Za-z0-9]{0,5})(?![\w])")
CLASSIFY_MARKER = "TIMONEL_CLASSIFY"


@dataclass(frozen=True)
class Classification:
    complexity: int
    kind: Kind
    risk: Risk
    needs_write: bool
    estimated_files: int
    source: Literal["model", "heuristic", "forced"] = "heuristic"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Classification:
        return Classification(
            complexity=int(data["complexity"]),
            kind=cast(Kind, data["kind"]),
            risk=cast(Risk, data["risk"]),
            needs_write=bool(data["needs_write"]),
            estimated_files=int(data["estimated_files"]),
            source=data.get("source", "heuristic"),
            reason=str(data.get("reason", "")),
        )


def extract_json_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    for start in (index for index, char in enumerate(text) if char == "{"):
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        return value
    return None


def schema_errors(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["expected a JSON object"]
    errors = []
    expected = {"complexity", "kind", "risk", "needs_write", "estimated_files"}
    missing = expected - data.keys()
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    complexity = data.get("complexity")
    if "complexity" in data and (
        isinstance(complexity, bool) or not isinstance(complexity, int) or not 1 <= complexity <= 4
    ):
        errors.append("complexity must be an integer from 1 to 4")
    if "kind" in data and data["kind"] not in KINDS:
        errors.append(f"kind must be one of {', '.join(KINDS)}")
    if "risk" in data and data["risk"] not in RISKS:
        errors.append(f"risk must be one of {', '.join(RISKS)}")
    if "needs_write" in data and not isinstance(data["needs_write"], bool):
        errors.append("needs_write must be a boolean")
    files = data.get("estimated_files")
    if "estimated_files" in data and (
        isinstance(files, bool) or not isinstance(files, int) or not 0 <= files <= 10000
    ):
        errors.append("estimated_files must be an integer from 0 to 10000")
    return errors


def parse_model_answer(text: str) -> Classification | None:
    data = extract_json_object(text)
    if schema_errors(data):
        return None
    kind = cast(Kind, data["kind"])
    return Classification(
        complexity=data["complexity"],
        kind=kind,
        risk=data["risk"],
        needs_write=data["needs_write"] and kind not in READ_ONLY_KINDS,
        estimated_files=data["estimated_files"],
        source="model",
        reason="classified by model",
    )


@cache
def default_heuristics() -> Heuristics:
    from timonel.config import validate_text

    return validate_text("").classifier.heuristics


@cache
def default_template() -> str:
    from timonel.config import validate_text

    return validate_text("").classifier.prompt_template


def detect_kind(lowered: str, settings: Heuristics) -> Kind:
    best: tuple[int, Kind] | None = None
    keywords = settings.keywords
    for kind in KEYWORD_KINDS:
        words = getattr(keywords, kind)
        positions = [lowered.find(word.lower()) for word in words if word.lower() in lowered]
        if positions and (best is None or min(positions) < best[0]):
            best = (min(positions), kind)
    if best:
        return best[1]
    words = re.findall(r"\w+", lowered)
    chat = {word.lower() for word in settings.chat_words}
    return "explain" if words and chat and all(word in chat for word in words) else "edit"


def mentioned_files(task: str) -> list[str]:
    return sorted(set(FILE_RE.findall(task)))


def contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word.lower() in text for word in words)


def heuristic(task: str, settings: Heuristics | None = None) -> Classification:
    rules = settings or default_heuristics()
    lowered = " ".join(task.lower().split())
    kind = detect_kind(lowered, rules)
    files = mentioned_files(task)
    complexity = getattr(rules.base_complexity, kind)
    notes = [f"kind '{kind}' from keywords"]
    if len(lowered) > rules.long_task_chars and rules.long_task_bonus:
        complexity += rules.long_task_bonus
        notes.append("long description")
    if len(files) >= rules.many_files and rules.many_files_bonus:
        complexity += rules.many_files_bonus
        notes.append(f"{len(files)} files mentioned")
    if contains_any(lowered, rules.broad_words) and rules.broad_bonus:
        complexity += rules.broad_bonus
        notes.append("broad scope")
    trivial = contains_any(lowered, rules.trivial_words) and len(lowered) < rules.trivial_max_chars
    if trivial and rules.trivial_penalty:
        complexity -= rules.trivial_penalty
        notes.append("trivial change")
    complexity = max(1, min(4, complexity))
    risky = contains_any(lowered, rules.risky_words)
    risk: Risk = "high" if risky else "medium" if complexity >= 3 else "low"
    return Classification(
        complexity=complexity,
        kind=kind,
        risk=risk,
        needs_write=kind not in READ_ONLY_KINDS,
        estimated_files=max(len(files), 1 if kind not in READ_ONLY_KINDS else 0),
        source="heuristic",
        reason="; ".join(notes),
    )


def fill_template(template: str, values: dict[str, str]) -> str:
    return re.sub(
        r"\{([a-z_]+)\}", lambda match: values.get(match.group(1), match.group(0)), template
    )


def classifier_prompt(task: str, template: str | None = None) -> str:
    return fill_template(template or default_template(), {"marker": CLASSIFY_MARKER, "task": task})


class ClassificationFailed(Exception):
    pass


def classify(
    task: str,
    ask_model: Callable[[str], str | None] | None = None,
    settings: Heuristics | None = None,
    template: str | None = None,
    retries: int = 0,
    on_failure: str = "heuristic",
) -> Classification:
    if ask_model is not None:
        prompt = classifier_prompt(task, template)
        for _ in range(retries + 1):
            try:
                answer = ask_model(prompt)
            except Exception:
                answer = None
            if answer is not None and (parsed := parse_model_answer(answer)) is not None:
                return parsed
        if on_failure == "fail":
            raise ClassificationFailed("no classifier returned a valid classification")
    fallback = heuristic(task, settings)
    if ask_model is None:
        return fallback
    return Classification(
        **{**fallback.to_dict(), "reason": f"model unavailable or invalid; {fallback.reason}"}
    )
