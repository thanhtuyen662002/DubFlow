"""Explicit version dispatch for public Review Center artifacts."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from .production import REASONS, ReviewError, ReviewFinding, ReviewSnapshot, RegenerationPlan


def _object(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ReviewError(f"{name} has missing or unsupported fields")
    return value


def finding_from_dict(value: object, *, legacy: bool = False) -> ReviewFinding:
    keys = {"finding_id", "reason", "range", "source_text", "translated_text", "speaker", "voice",
            "output_preview", "confidence", "affected_stages"}
    raw = _object(value, keys if legacy else keys | {"cue_id", "warning", "confidence_source"}, "finding")
    interval = raw["range"]
    if interval is None and not legacy:
        start = end = None
    else:
        interval = _object(interval, {"start_ticks", "end_ticks"}, "finding range")
        start, end = interval["start_ticks"], interval["end_ticks"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise ReviewError("wire ticks must be decimal strings")
    stages = raw["affected_stages"]
    if not isinstance(stages, list):
        raise ReviewError("affected_stages must be an array")
    if raw["confidence"] is not None and not isinstance(raw["confidence"], str):
        raise ReviewError("wire confidence must be a decimal string")
    if legacy and raw["confidence"] is None:
        raise ReviewError("v1 confidence cannot be null")
    return ReviewFinding(raw["finding_id"], raw["reason"], start, end, raw["source_text"],
                         raw["translated_text"], raw["speaker"], raw["voice"], raw["output_preview"],
                         raw["confidence"], tuple(stages), None if legacy else raw["cue_id"],
                         None if legacy else raw["warning"], None if legacy else raw["confidence_source"])


def plan_from_dict(value: object) -> RegenerationPlan | None:
    if value is None:
        return None
    raw = _object(value, {"finding_id", "stages_to_rerun", "preserved_artifacts", "replacement_artifacts",
                          "retain_previous_until_validated", "requires_full_manual_review"}, "regeneration plan")
    for name in ("stages_to_rerun", "preserved_artifacts", "replacement_artifacts"):
        if not isinstance(raw[name], list):
            raise ReviewError(f"{name} must be an array")
    return RegenerationPlan(raw["finding_id"], tuple(raw["stages_to_rerun"]), tuple(raw["preserved_artifacts"]),
                            tuple(raw["replacement_artifacts"]), raw["retain_previous_until_validated"],
                            raw["requires_full_manual_review"])


@dataclass(frozen=True)
class LegacyReviewPayload:
    """Display-only v1 data. No fabricated job/time-base/cue admission."""
    encoded: str

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.encoded)


def _validate_legacy_finding(value: object) -> str:
    raw = _object(value, {"finding_id", "reason", "range", "source_text", "translated_text", "speaker",
                          "voice", "output_preview", "confidence", "affected_stages"}, "v1 finding")
    if not isinstance(raw["finding_id"], str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", raw["finding_id"]) is None:
        raise ReviewError("v1 finding_id is invalid")
    if not isinstance(raw["reason"], str) or raw["reason"] not in REASONS:
        raise ReviewError("v1 reason is invalid")
    interval = _object(raw["range"], {"start_ticks", "end_ticks"}, "v1 range")
    for tick in interval.values():
        if not isinstance(tick, str) or len(tick) > 20 or re.fullmatch(r"(0|-?[1-9][0-9]*)", tick) is None:
            raise ReviewError("v1 tick is invalid")
    for name, limit, nullable in (("source_text", 8192, False), ("translated_text", 8192, False),
                                 ("speaker", 256, True), ("voice", 256, True), ("output_preview", 4096, True)):
        text = raw[name]
        if text is None and nullable:
            continue
        if not isinstance(text, str) or len(text) > limit:
            raise ReviewError(f"v1 {name} is invalid")
    if not isinstance(raw["confidence"], str) or re.fullmatch(r"(0|0\.[0-9]+|1(?:\.0+)?)", raw["confidence"]) is None:
        raise ReviewError("v1 confidence is invalid")
    stages = raw["affected_stages"]
    if not isinstance(stages, list) or not 1 <= len(stages) <= 64 or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in stages):
        raise ReviewError("v1 affected_stages is invalid")
    return raw["finding_id"]


def read_review_snapshot(value: object) -> ReviewSnapshot | LegacyReviewPayload:
    if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int:
        raise ReviewError("review schema_version is missing or invalid")
    version = value["schema_version"]
    if version == 1:
        raw = _object(value, {"schema_version", "findings"}, "v1 review payload")
    elif version == 2:
        raw = _object(value, {"schema_version", "job_id", "revision", "time_base", "findings", "last_plan"}, "v2 review snapshot")
    else:
        raise ReviewError("unsupported review schema_version")
    if not isinstance(raw["findings"], list) or len(raw["findings"]) > 100000:
        raise ReviewError("review findings must be a bounded array")
    if version == 1:
        ids = [_validate_legacy_finding(item) for item in raw["findings"]]
        if len(set(ids)) != len(ids):
            raise ReviewError("duplicate review finding id")
        return LegacyReviewPayload(json.dumps(raw, ensure_ascii=False))
    findings = tuple(finding_from_dict(item) for item in raw["findings"])
    if len({item.finding_id for item in findings}) != len(findings):
        raise ReviewError("duplicate review finding id")
    base = _object(raw["time_base"], {"numerator", "denominator"}, "time_base")
    if any(not isinstance(base[name], str) for name in base):
        raise ReviewError("wire time_base factors must be strings")
    plan = plan_from_dict(raw["last_plan"])
    snapshot = ReviewSnapshot(raw["job_id"], raw["revision"], findings, plan, (base["numerator"], base["denominator"]))
    if plan is not None and plan.finding_id not in {item.finding_id for item in findings}:
        raise ReviewError("plan references an unknown finding")
    return snapshot


def decode_review_json(value: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, item in items:
            if key in result:
                raise ReviewError("duplicate JSON object key")
            result[key] = item
        return result

    def constant(value: str) -> None:
        raise ReviewError("non-finite JSON value")

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError) as error:
        raise ReviewError(f"review JSON is invalid: {error}") from error
