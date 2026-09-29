"""Pure, dependency-free QC provenance and confidence calibration policy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import math
import re
from typing import Iterable, Sequence


QC_CONTRACT_VERSION = "qc-v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_DECIMAL = re.compile(r"^(0|0\.[0-9]+|1(?:\.0+)?)$")


class QCError(ValueError):
    pass


class QCStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    STALE = "STALE"


def _text(value: object, name: str, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
        raise QCError(f"{name} must be non-empty, bounded and control-free")
    return value.strip()


def _hash(value: object, name: str) -> str:
    value = _text(value, name, 64)
    if not _HASH.fullmatch(value):
        raise QCError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise QCError(f"{name} must be a finite confidence value")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise QCError(f"{name} must be finite")
        parsed = Decimal(str(value))
    elif isinstance(value, str) and _DECIMAL.fullmatch(value):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise QCError(f"{name} is not a decimal") from exc
    else:
        raise QCError(f"{name} must be a decimal in [0, 1]")
    if not parsed.is_finite() or parsed < Decimal("0") or parsed > Decimal("1"):
        raise QCError(f"{name} must be in [0, 1]")
    return parsed


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    return format(value.normalize(), "f")


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ThresholdSet:
    calibration_id: str
    low_confidence: str
    warn_confidence: str
    fail_confidence: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "calibration_id", _text(self.calibration_id, "calibration_id"))
        low = _decimal(self.low_confidence, "low_confidence")
        warn = _decimal(self.warn_confidence, "warn_confidence")
        fail = _decimal(self.fail_confidence, "fail_confidence")
        if not low <= warn <= fail:
            raise QCError("thresholds must satisfy low <= warn <= fail")
        object.__setattr__(self, "low_confidence", _decimal_string(low))
        object.__setattr__(self, "warn_confidence", _decimal_string(warn))
        object.__setattr__(self, "fail_confidence", _decimal_string(fail))

    @property
    def hash(self) -> str:
        return _hash_json({"calibration_id": self.calibration_id, "low": self.low_confidence, "warn": self.warn_confidence, "fail": self.fail_confidence})


@dataclass(frozen=True)
class CalibrationProfile:
    calibration_id: str
    model_id: str
    model_version: str
    dataset_revision: str
    metric: str
    thresholds: ThresholdSet

    def __post_init__(self) -> None:
        for name in ("calibration_id", "model_id", "model_version", "dataset_revision", "metric"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 256))
        if self.thresholds.calibration_id != self.calibration_id:
            raise QCError("threshold calibration_id must match profile")


@dataclass(frozen=True)
class ConfidenceBin:
    lower_inclusive: str
    upper_inclusive: str
    count: int
    mean_confidence: str

    def __post_init__(self) -> None:
        lower = _decimal(self.lower_inclusive, "bin.lower_inclusive")
        upper = _decimal(self.upper_inclusive, "bin.upper_inclusive")
        if lower >= upper:
            raise QCError("confidence bin must have positive width")
        if type(self.count) is not int or not 0 <= self.count <= (1 << 64) - 1:
            raise QCError("bin.count is outside the u64 range")
        mean = _decimal(self.mean_confidence, "bin.mean_confidence")
        if mean < lower or mean > upper:
            raise QCError("bin mean must be within bin bounds")
        object.__setattr__(self, "lower_inclusive", _decimal_string(lower))
        object.__setattr__(self, "upper_inclusive", _decimal_string(upper))
        object.__setattr__(self, "mean_confidence", _decimal_string(mean))


def calibrate_confidence(scores: Sequence[object], *, bins: int = 10) -> tuple[ConfidenceBin, ...]:
    if type(bins) is not int or not 1 <= bins <= 100:
        raise QCError("bins must be between 1 and 100")
    values = sorted(_decimal(score, "score") for score in scores)
    if not values:
        raise QCError("at least one score is required")
    result: list[ConfidenceBin] = []
    width = Decimal(1) / Decimal(bins)
    for index in range(bins):
        lower = width * Decimal(index)
        upper = Decimal(1) if index == bins - 1 else width * Decimal(index + 1)
        members = [value for value in values if (lower <= value <= upper if index == bins - 1 else lower <= value < upper)]
        mean = sum(members, Decimal(0)) / Decimal(len(members)) if members else lower
        result.append(ConfidenceBin(_decimal_string(lower), _decimal_string(upper), len(members), _decimal_string(mean)))
    return tuple(result)


@dataclass(frozen=True)
class Finding:
    finding_id: str
    scope: str
    code: str
    severity: str
    confidence: str
    message: str

    def __post_init__(self) -> None:
        for name in ("finding_id", "scope", "code"):
            value = _text(getattr(self, name), name, 256)
            if name == "finding_id" and not _ID.fullmatch(value):
                raise QCError("finding_id has an invalid format")
            object.__setattr__(self, name, value)
        severity = _text(self.severity, "severity", 16).upper()
        if severity not in {"INFO", "WARN", "FAIL"}:
            raise QCError("severity must be INFO, WARN or FAIL")
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "confidence", _decimal_string(_decimal(self.confidence, "confidence")))
        object.__setattr__(self, "message", _text(self.message, "message"))

    def to_dict(self) -> dict[str, str]:
        return {"finding_id": self.finding_id, "scope": self.scope, "code": self.code, "severity": self.severity, "confidence": self.confidence, "message": self.message}


@dataclass(frozen=True)
class QCReport:
    report_id: str
    producer_version: str
    upstream_hashes: tuple[str, ...]
    config_hash: str
    model_id: str
    model_version: str
    calibration: CalibrationProfile
    thresholds: ThresholdSet
    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.report_id):
            raise QCError("report_id has an invalid format")
        object.__setattr__(self, "producer_version", _text(self.producer_version, "producer_version", 256))
        hashes = tuple(sorted({_hash(value, "upstream_hash") for value in self.upstream_hashes}))
        object.__setattr__(self, "upstream_hashes", hashes)
        object.__setattr__(self, "config_hash", _hash(self.config_hash, "config_hash"))
        object.__setattr__(self, "model_id", _text(self.model_id, "model_id", 256))
        object.__setattr__(self, "model_version", _text(self.model_version, "model_version", 256))
        if self.calibration.model_id != self.model_id or self.calibration.model_version != self.model_version:
            raise QCError("calibration model does not match QC model")
        if self.thresholds != self.calibration.thresholds:
            raise QCError("QC thresholds must equal calibration thresholds")

    @property
    def status(self) -> QCStatus:
        if any(finding.severity == "FAIL" for finding in self.findings):
            return QCStatus.FAIL
        if any(finding.severity == "WARN" for finding in self.findings):
            return QCStatus.WARN
        return QCStatus.PASS

    def is_current(self, *, upstream_hashes: Iterable[str], config_hash: str, model_id: str, model_version: str, calibration_id: str, threshold_set_hash: str) -> bool:
        return (
            self.upstream_hashes == tuple(sorted({_hash(value, "upstream_hash") for value in upstream_hashes}))
            and self.config_hash == _hash(config_hash, "config_hash")
            and self.model_id == _text(model_id, "model_id", 256)
            and self.model_version == _text(model_version, "model_version", 256)
            and self.calibration.calibration_id == _text(calibration_id, "calibration_id", 256)
            and self.thresholds.hash == _hash(threshold_set_hash, "threshold_set_hash")
        )

    def current_status(self, **request: object) -> QCStatus:
        return self.status if self.is_current(**request) else QCStatus.STALE

    def to_dict(self) -> dict[str, object]:
        confidences = [Decimal(finding.confidence) for finding in self.findings]
        summary = {
            "item_count": len(self.findings),
            "pass_count": sum(1 for finding in self.findings if finding.severity == "INFO"),
            "warn_count": sum(1 for finding in self.findings if finding.severity == "WARN"),
            "fail_count": sum(1 for finding in self.findings if finding.severity == "FAIL"),
            "low_confidence_count": sum(1 for confidence in confidences if confidence < Decimal(self.thresholds.low_confidence)),
        }
        return {
            "schema_version": 1,
            "report_id": self.report_id,
            "status": self.status.value,
            "provenance": {
                "producer_version": self.producer_version,
                "contract_version": QC_CONTRACT_VERSION,
                "upstream_hashes": list(self.upstream_hashes),
                "config_hash": self.config_hash,
                "model_id": self.model_id,
                "model_version": self.model_version,
                "calibration_id": self.calibration.calibration_id,
                "threshold_set_hash": self.thresholds.hash,
            },
            "summary": summary,
            "findings": [finding.to_dict() for finding in self.findings],
        }
