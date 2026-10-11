"""Test-only validation of every keyword used by the local review schemas.

This is a restricted schema assertion helper, not a general JSON Schema library.
Unsupported keywords fail explicitly; external/network references are forbidden.
"""
import json
import math
from pathlib import Path
import re

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts/review"
KEYWORDS = {"$schema", "$id", "$defs", "title", "$ref", "type", "required", "additionalProperties",
            "properties", "const", "enum", "pattern", "minLength", "maxLength", "minimum", "maximum",
            "minItems", "maxItems", "items"}


def assert_schema(value, filename):
    path = CONTRACTS / filename
    schema = json.loads(path.read_text(encoding="utf-8"))
    _validate(value, schema, schema, path, "$", 0)


def _validate(value, spec, document, path, location, depth):
    assert depth < 64, "schema reference depth exceeded"
    assert not (set(spec) - KEYWORDS), "unsupported schema keywords: " + str(set(spec) - KEYWORDS)
    if "$ref" in spec:
        name, _, fragment = spec["$ref"].partition("#")
        if name:
            reference = (path.parent / name).resolve()
            assert reference.parent == CONTRACTS.resolve(), "external schema reference"
            document = json.loads(reference.read_text(encoding="utf-8"))
            path = reference
        target = document
        for key in fragment.split("/")[1:]:
            target = target[key.replace("~1", "/").replace("~0", "~")]
        _validate(value, target, document, path, location, depth + 1)
    if "type" in spec:
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
                   "string": isinstance(value, str), "integer": type(value) is int,
                   "number": type(value) in (int, float) and math.isfinite(value),
                   "null": value is None, "boolean": type(value) is bool}
        assert all(item in matches for item in types), "unsupported JSON type"
        assert any(matches[item] for item in types), location + ": type mismatch"
    if "const" in spec:
        assert value == spec["const"] and type(value) is type(spec["const"]), location + ": const mismatch"
    if "enum" in spec:
        assert value in spec["enum"], location + ": enum mismatch"
    if isinstance(value, dict):
        assert set(spec.get("required", [])) <= set(value), location + ": missing fields"
        properties = spec.get("properties", {})
        if spec.get("additionalProperties") is False:
            assert set(value) <= set(properties), location + ": extra fields"
        for name, child in properties.items():
            if name in value:
                _validate(value[name], child, document, path, location + "." + name, depth + 1)
    if isinstance(value, str):
        assert len(value) >= spec.get("minLength", 0), location + ": too short"
        assert len(value) <= spec.get("maxLength", len(value)), location + ": too long"
        if "pattern" in spec:
            assert re.search(spec["pattern"], value) is not None, location + ": pattern mismatch"
    if type(value) in (int, float):
        assert value >= spec.get("minimum", value), location + ": below minimum"
        assert value <= spec.get("maximum", value), location + ": above maximum"
    if isinstance(value, list):
        assert len(value) >= spec.get("minItems", 0), location + ": too few items"
        assert len(value) <= spec.get("maxItems", len(value)), location + ": too many items"
        if "items" in spec:
            for index, item in enumerate(value):
                _validate(item, spec["items"], document, path, f"{location}[{index}]", depth + 1)
