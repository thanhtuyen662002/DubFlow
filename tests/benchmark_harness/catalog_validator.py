from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
SIGNED_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class CatalogError(ValueError):
    pass


def _required(mapping: dict[str, Any], key: str, kind: type) -> Any:
    value = mapping.get(key)
    if not isinstance(value, kind):
        raise CatalogError(f"{key} must be a {kind.__name__}")
    return value


def _decimal(value: Any, name: str, *, signed: bool = False) -> None:
    pattern = SIGNED_DECIMAL if signed else DECIMAL
    if not isinstance(value, str) or len(value) > 20 or not pattern.fullmatch(value):
        raise CatalogError(f"{name} must be a bounded decimal string")


def _asset(asset: Any) -> None:
    if not isinstance(asset, dict):
        raise CatalogError("asset must be an object")
    kind = _required(asset, "kind", str)
    if kind not in {"generated", "external"}:
        raise CatalogError("asset.kind is unsupported")
    path = _required(asset, "path", str)
    if not path or ".." in Path(path).parts or "\\" in path or any(ord(char) < 32 for char in path):
        raise CatalogError("asset.path must be a safe repository-relative path")
    digest = _required(asset, "sha256", str)
    if not SHA256.fullmatch(digest):
        raise CatalogError("asset.sha256 must be a sha256 digest")
    _decimal(_required(asset, "size_bytes", str), "asset.size_bytes")
    fetch_uri = asset.get("fetch_uri")
    if kind == "external" and (not isinstance(fetch_uri, str) or not fetch_uri.startswith(("https://", "http://"))):
        raise CatalogError("external assets require an http(s) fetch_uri")
    if kind == "generated" and fetch_uri is not None:
        raise CatalogError("generated assets must not carry a fetch_uri")


def validate_catalog(data: dict[str, Any], *, root: Path | None = None) -> tuple[str, ...]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    catalog_id = data.get("catalog_id")
    if not isinstance(catalog_id, str) or not catalog_id:
        errors.append("catalog_id is required")
    fixtures = data.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        return tuple(errors + ["fixtures must be a non-empty list"])
    seen: set[str] = set()
    found = {"portrait": False, "landscape": False, "vfr": False, "nonzero_pts": False, "rotation": False, "text": False, "speaker": False}
    for index, fixture in enumerate(fixtures):
        prefix = f"fixtures[{index}]"
        if not isinstance(fixture, dict):
            errors.append(f"{prefix} must be an object")
            continue
        fixture_id = fixture.get("fixture_id")
        if not isinstance(fixture_id, str) or not fixture_id or fixture_id in seen:
            errors.append(f"{prefix}.fixture_id must be unique and non-empty")
        else:
            seen.add(fixture_id)
        try:
            _asset(fixture.get("asset"))
            geometry = _required(fixture, "geometry", dict)
            width = _required(geometry, "width", int)
            height = _required(geometry, "height", int)
            if width < 1 or height < 1:
                raise CatalogError(f"{prefix}.geometry dimensions must be positive")
            rotation = geometry.get("rotation_degrees")
            if rotation not in {0, 90, 180, 270}:
                raise CatalogError(f"{prefix}.geometry.rotation_degrees is invalid")
            found["portrait"] |= height > width
            found["landscape"] |= width > height
            found["rotation"] |= rotation != 0
            timeline = _required(fixture, "timeline", dict)
            time_base = _required(timeline, "time_base", dict)
            _decimal(_required(time_base, "numerator", str), f"{prefix}.time_base.numerator")
            denominator = _required(time_base, "denominator", str)
            _decimal(denominator, f"{prefix}.time_base.denominator")
            if denominator == "0":
                raise CatalogError(f"{prefix}.time_base.denominator must be positive")
            _decimal(_required(timeline, "duration_ticks", str), f"{prefix}.duration_ticks")
            pts = _required(timeline, "pts_start_ticks", str)
            _decimal(pts, f"{prefix}.pts_start_ticks", signed=True)
            found["nonzero_pts"] |= pts != "0"
            vfr = _required(timeline, "variable_frame_rate", bool)
            found["vfr"] |= vfr
            frame_durations = timeline.get("frame_durations_ticks")
            if frame_durations is not None:
                if not isinstance(frame_durations, list) or not all(isinstance(item, str) and DECIMAL.fullmatch(item) for item in frame_durations):
                    raise CatalogError(f"{prefix}.frame_durations_ticks is invalid")
            annotations = _required(fixture, "annotations", dict)
            text_cases = _required(annotations, "text_cases", list)
            speaker_cases = _required(annotations, "speaker_cases", list)
            allowed_text = {"dialogue", "watermark", "signage", "danmaku", "vertical", "diagonal", "multi_text"}
            allowed_speaker = {"offscreen", "overlap", "same_actor_multiple_characters", "narrator"}
            if not set(text_cases) <= allowed_text or not set(speaker_cases) <= allowed_speaker:
                raise CatalogError(f"{prefix}.annotations contains an unknown case")
            found["text"] |= bool(text_cases)
            found["speaker"] |= bool(speaker_cases)
            if root is not None and fixture["asset"]["kind"] == "generated":
                asset_path = root / fixture["asset"]["path"]
                if not asset_path.is_file():
                    raise CatalogError(f"{prefix}.generated asset is missing: {asset_path}")
                digest = "sha256:" + hashlib.sha256(asset_path.read_bytes()).hexdigest()
                if digest != fixture["asset"]["sha256"]:
                    raise CatalogError(f"{prefix}.generated asset hash does not match catalog")
                if str(asset_path.stat().st_size) != fixture["asset"]["size_bytes"]:
                    raise CatalogError(f"{prefix}.generated asset size does not match catalog")
        except CatalogError as exc:
            errors.append(f"{prefix}: {exc}")
    if fixtures and not all(found.values()):
        errors.append("catalog does not cover all required geometry/timeline/text/speaker edge cases")
    return tuple(errors)


def load_catalog(path: Path, *, verify_generated_assets: bool = True) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_catalog(data, root=path.parents[2] if verify_generated_assets else None)
    if errors:
        raise CatalogError("; ".join(errors))
    return data
