from __future__ import annotations

import copy
import difflib
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import yaml

Path_ = tuple[str, ...]
Locate = Callable[[Path_], str]
TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


@cache
def load_schema() -> dict[str, Any]:
    text = (
        resources.files("timonel.schema").joinpath("config.schema.yaml").read_text(encoding="utf-8")
    )
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def schema_json() -> str:
    return json.dumps(load_schema(), indent=2, ensure_ascii=False) + "\n"


def resolve(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target = node["$ref"].removeprefix("#/").split("/")
        found: Any = root
        for part in target:
            found = found[part]
        merged = {**found, **{key: value for key, value in node.items() if key != "$ref"}}
        node = merged
    return node


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object" if isinstance(value, dict) else type(value).__name__


def suggestion(word: str, options: list[str]) -> str:
    close = difflib.get_close_matches(word, options, n=1, cutoff=0.6)
    return f"; did you mean {close[0]!r}?" if close else ""


@dataclass
class Problem:
    path: Path_
    message: str


def check(value: Any, node: dict[str, Any], path: Path_, root: dict[str, Any]) -> Iterator[Problem]:
    node = resolve(node, root)
    expected = node.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(TYPE_CHECKS[option](value) for option in options):
            yield Problem(
                path, f"expected {' or '.join(options)}, got {type_name(value)} {value!r}"[:300]
            )
            return
    if "enum" in node and value not in node["enum"]:
        listed = ", ".join(str(option) for option in node["enum"])
        hint = suggestion(str(value), [str(option) for option in node["enum"]])
        yield Problem(path, f"expected one of {listed}, got {value!r}{hint}")
        return
    yield from check_scalar(value, node, path)
    if isinstance(value, dict):
        yield from check_object(value, node, path, root)
    if isinstance(value, list):
        yield from check_array(value, node, path, root)


def check_scalar(value: Any, node: dict[str, Any], path: Path_) -> Iterator[Problem]:
    numeric = isinstance(value, int | float) and not isinstance(value, bool)
    if numeric and "minimum" in node and value < node["minimum"]:
        yield Problem(path, f"must be at least {node['minimum']}, got {value}")
    if numeric and "maximum" in node and value > node["maximum"]:
        yield Problem(path, f"must be at most {node['maximum']}, got {value}")
    if isinstance(value, str):
        if "minLength" in node and len(value) < node["minLength"]:
            yield Problem(path, f"must have at least {node['minLength']} characters")
        if "maxLength" in node and len(value) > node["maxLength"]:
            yield Problem(path, f"must have at most {node['maxLength']} characters")
        if "pattern" in node and not re.search(node["pattern"], value):
            yield Problem(path, f"does not match the expected format {node['pattern']!r}")
        if node.get("format") == "regex":
            try:
                re.compile(value)
            except re.error as error:
                yield Problem(path, f"invalid regular expression: {error}")


def check_object(
    value: dict[str, Any], node: dict[str, Any], path: Path_, root: dict[str, Any]
) -> Iterator[Problem]:
    properties: dict[str, Any] = node.get("properties", {})
    for name in node.get("required", []):
        if name not in value:
            yield Problem(path, f"missing required field {name!r}")
    names = node.get("propertyNames")
    extra = node.get("additionalProperties", True)
    for key, item in value.items():
        key_path = (*path, str(key))
        if names is not None:
            yield from check(str(key), names, key_path, root)
        if key in properties:
            yield from check(item, properties[key], key_path, root)
        elif extra is False:
            yield Problem(key_path, f"unknown field{suggestion(str(key), list(properties))}")
        elif isinstance(extra, dict):
            yield from check(item, extra, key_path, root)


def check_array(
    value: list[Any], node: dict[str, Any], path: Path_, root: dict[str, Any]
) -> Iterator[Problem]:
    if "minItems" in node and len(value) < node["minItems"]:
        yield Problem(path, f"needs at least {node['minItems']} items")
    if "maxItems" in node and len(value) > node["maxItems"]:
        yield Problem(path, f"allows at most {node['maxItems']} items")
    if node.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(
        value
    ):
        yield Problem(path, "items must be unique")
    if "items" in node:
        for index, item in enumerate(value):
            yield from check(item, node["items"], (*path, str(index)), root)


def validate(data: Any, schema: dict[str, Any] | None = None) -> list[Problem]:
    root = schema or load_schema()
    return list(check(data, root, (), root))


def defaults(node: dict[str, Any] | None = None, root: dict[str, Any] | None = None) -> Any:
    root = root or load_schema()
    node = resolve(node or root, root)
    if "default" in node:
        return copy.deepcopy(node["default"])
    if node.get("type") == "object" and "properties" in node:
        built = {}
        for key, child in node["properties"].items():
            value = defaults(child, root)
            if value is not None or "default" in resolve(child, root):
                built[key] = value
        return built
    return None


def node_at(path: Path_, root: dict[str, Any] | None = None) -> dict[str, Any] | None:
    root = root or load_schema()
    node = resolve(root, root)
    for part in path:
        properties = node.get("properties", {})
        if part in properties:
            node = resolve(properties[part], root)
        elif isinstance(node.get("additionalProperties"), dict):
            node = resolve(node["additionalProperties"], root)
        elif node.get("type") == "array" and isinstance(node.get("items"), dict) and part.isdigit():
            node = resolve(node["items"], root)
        else:
            return None
    return node


@dataclass(frozen=True)
class Option:
    path: Path_
    node: dict[str, Any]
    area: str
    advanced: bool

    @property
    def key(self) -> str:
        return ".".join(self.path)

    def description(self, language: str) -> str:
        translated = self.node.get("x-i18n", {}).get(language)
        return str(translated or self.node.get("description", ""))


def options(root: dict[str, Any] | None = None) -> list[Option]:
    root = root or load_schema()
    found: list[Option] = []

    def walk(node: dict[str, Any], path: Path_, area: str, advanced: bool) -> None:
        node = resolve(node, root)
        area = node.get("x-area", area)
        advanced = advanced or bool(node.get("x-advanced"))
        properties = node.get("properties")
        if node.get("type") == "object" and properties:
            for key, child in properties.items():
                walk(child, (*path, key), area, advanced)
            return
        if path:
            found.append(Option(path, node, area, advanced))

    walk(root, (), "general", False)
    return found
