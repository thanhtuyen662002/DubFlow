"""Strict, dependency-free validation for the canonical timeline fixtures."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "fixtures" / "timeline"
SCHEMA_PATH = ROOT / "contracts" / "timeline" / "schema-v1.json"
SIGNED_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)$")
UNSIGNED_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")


def ensure_object(value: Any, name: str, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{name} must be an object")
    keys = set(value)
    allowed = required | optional
    unknown = keys - allowed
    missing = required - keys
    if unknown:
        raise ValueError(f"{name} has unknown members: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{name} is missing members: {sorted(missing)}")
    return value


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decimal(value: Any, *, signed: bool) -> int:
    if type(value) is not str:
        raise ValueError("wide integer fields must be decimal strings")
    pattern = SIGNED_DECIMAL if signed else UNSIGNED_DECIMAL
    if not pattern.fullmatch(value):
        raise ValueError(f"non-canonical decimal string: {value!r}")
    parsed = int(value)
    if signed and not -(2**63) <= parsed <= 2**63 - 1:
        raise ValueError("signed tick is outside i64 range")
    if not signed and not 0 < parsed <= 2**64 - 1:
        raise ValueError("time-base factors and dimensions must be positive")
    return parsed


def time_point(value: Any) -> tuple[Fraction, int]:
    value = ensure_object(value, "time point", {"kind", "schema_version", "ticks", "time_base"})
    if type(value["kind"]) is not str or value["kind"] != "time_point":
        raise ValueError("time point discriminator/version mismatch")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("time point discriminator/version mismatch")
    ticks = decimal(value["ticks"], signed=True)
    base = ensure_object(value["time_base"], "time base", {"numerator", "denominator"})
    numerator = decimal(base["numerator"], signed=False)
    denominator = decimal(base["denominator"], signed=False)
    rational = Fraction(ticks * numerator, denominator)
    return rational, ticks


def validate_mapping(mapping: Any) -> None:
    mapping = ensure_object(mapping, "proxy mapping", {"kind", "segments"})
    if type(mapping["kind"]) is not str or mapping["kind"] != "proxy_source_mapping":
        raise ValueError("proxy mapping discriminator mismatch")
    segments = mapping["segments"]
    if type(segments) is not list or not segments:
        raise ValueError("proxy mapping needs at least one segment")
    previous: tuple[Fraction, Fraction] | None = None
    for segment in segments:
        segment = ensure_object(
            segment,
            "mapping segment",
            {"source_start", "source_end", "proxy_start", "proxy_end"},
        )
        source_start, _ = time_point(segment["source_start"])
        source_end, _ = time_point(segment["source_end"])
        proxy_start, _ = time_point(segment["proxy_start"])
        proxy_end, _ = time_point(segment["proxy_end"])
        if not source_start < source_end or not proxy_start < proxy_end:
            raise ValueError("mapping segments are half-open and must have positive duration")
        if previous is not None and (previous[0] > proxy_start or previous[1] > source_start):
            raise ValueError("mapping segments overlap or are out of order")
        previous = (proxy_end, source_end)


def validate_geometry(geometry: Any) -> None:
    geometry = ensure_object(
        geometry,
        "geometry",
        {"kind", "coded_dimensions", "rotation_degrees", "pixel_aspect_ratio", "coordinate_convention"},
    )
    if type(geometry["kind"]) is not str or geometry["kind"] != "coded_to_display_geometry":
        raise ValueError("geometry discriminator mismatch")
    dimensions = ensure_object(geometry["coded_dimensions"], "coded dimensions", {"width", "height"})
    if decimal(dimensions["width"], signed=False) > 2**32 - 1 or decimal(dimensions["height"], signed=False) > 2**32 - 1:
        raise ValueError("coded dimensions are outside Rust u32 range")
    rotation = geometry["rotation_degrees"]
    if type(rotation) is not int or rotation not in {0, 90, 180, 270}:
        raise ValueError("rotation must be a clockwise quarter turn")
    aspect = ensure_object(geometry["pixel_aspect_ratio"], "pixel aspect ratio", {"numerator", "denominator"})
    if decimal(aspect["numerator"], signed=False) > 2**32 - 1 or decimal(aspect["denominator"], signed=False) > 2**32 - 1:
        raise ValueError("pixel aspect ratio is outside Rust u32 range")
    if type(geometry["coordinate_convention"]) is not str or geometry["coordinate_convention"] != "pixel_bounds":
        raise ValueError("coordinate convention must be pixel_bounds")


def validate_fixture(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    value = ensure_object(
        value,
        "fixture",
        {"schema_version", "kind", "source_pts", "proxy_mapping", "geometry"},
        {"timing"},
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("fixture discriminator/version mismatch")
    if type(value["kind"]) is not str or value["kind"] != "timeline_fixture":
        raise ValueError("fixture discriminator/version mismatch")
    timing = value.get("timing")
    if timing is not None and (type(timing) is not str or timing not in {"cfr", "vfr"}):
        raise ValueError("fixture discriminator/version mismatch")
    points = value["source_pts"]
    if type(points) is not list or len(points) < 3:
        raise ValueError("fixture must contain at least three source PTS values")
    point_values = [time_point(point)[0] for point in points]
    if point_values != sorted(point_values) or len(set(point_values)) != len(point_values):
        raise ValueError("source PTS values must be strictly increasing")
    if timing == "vfr" and not any(time_point(point)[1] != 0 for point in points):
        raise ValueError("fixture must preserve a non-zero PTS")
    intervals = [right - left for left, right in zip(point_values, point_values[1:])]
    if timing == "vfr" and all(interval == intervals[0] for interval in intervals[1:]):
        raise ValueError("fixture must exercise irregular VFR spacing")
    if timing == "cfr" and any(interval != intervals[0] for interval in intervals[1:]):
        raise ValueError("CFR fixture must have regular timestamp spacing")
    validate_mapping(value.get("proxy_mapping"))
    validate_geometry(value.get("geometry"))


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    if type(schema) is not dict or schema.get("$id") != "https://dubflow.local/contracts/timeline/schema-v1.json":
        raise SystemExit("timeline schema document is missing its stable id")
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    if not paths:
        raise SystemExit("no timeline fixtures found")
    for path in paths:
        validate_fixture(path)
        print(f"validated {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
