"""The version-one update manifest trust and schema boundary."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import json
import re

from . import UpdateError


PUBLIC_KEY_B64 = "EhmZZeOFmRDqWLm7pyenErag7co+7NM9iPcmIhMATTs="
KEY_ID = "dubflow-update-v1"
# Kept identical to packaging.release.manifest's version identifier. Do not
# import the repo's packaging namespace into the runtime dependency namespace.
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
MAX_SETUP_BYTES = 2_000_000_000
RELEASE_URL = "https://github.com/thanhtuyen662002/DubFlow/releases/download"


def canonical_payload(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def setup_name(version: str) -> str:
    return f"DubFlow-{version}-windows-x64-setup.exe"


def setup_url(version: str) -> str:
    return f"{RELEASE_URL}/v{version}/{setup_name(version)}"


def _invalid(message: str) -> None:
    raise UpdateError("MANIFEST_INVALID", message)


def _validate_setup(setup: dict, version: str | None = None) -> None:
    if not isinstance(setup, dict) or set(setup) != {"name", "url", "sha256", "size_bytes"}:
        _invalid("setup must contain name, url, sha256 and size_bytes")
    name = setup["name"]
    if version is None:
        match = re.fullmatch(r"DubFlow-([A-Za-z0-9][A-Za-z0-9._+-]{0,63})-windows-x64-setup\.exe", name) if isinstance(name, str) else None
        if match is None:
            _invalid("invalid setup name")
        version = match.group(1)
    if name != setup_name(version) or setup["url"] != setup_url(version):
        _invalid("setup name and HTTPS URL must match the release version")
    if not isinstance(setup["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", setup["sha256"]):
        _invalid("setup sha256 must be 64 lowercase hexadecimal characters")
    if type(setup["size_bytes"]) is not int or not 1 <= setup["size_bytes"] <= MAX_SETUP_BYTES:
        _invalid("setup size_bytes is outside the supported range")


def _validate_payload(payload: dict) -> None:
    if set(payload) != {"schema_version", "version", "channel", "published_at_utc", "notes", "setup"}:
        _invalid("unexpected or missing update manifest fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        _invalid("schema_version must be 1")
    version = payload["version"]
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        _invalid("invalid version identifier")
    if not isinstance(payload["channel"], str) or payload["channel"] not in {"stable", "candidate"}:
        _invalid("channel must be stable or candidate")
    timestamp = payload["published_at_utc"]
    try:
        if not isinstance(timestamp, str) or "T" not in timestamp:
            raise ValueError("timestamp required")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
    except ValueError:
        _invalid("published_at_utc must be an ISO UTC timestamp")
    if not isinstance(payload["notes"], str) or len(payload["notes"]) > 4000:
        _invalid("notes must be a string of at most 4000 characters")
    _validate_setup(payload["setup"], version)


def verify_manifest(document: dict, public_key_b64: str = PUBLIC_KEY_B64) -> dict:
    """Return the authenticated payload; reject unknown schema and trust keys."""

    if not isinstance(document, dict):
        _invalid("update manifest must be an object")
    signature = document.get("signature")
    if (
        not isinstance(signature, dict)
        or set(signature) != {"algorithm", "key_id", "value"}
        or signature["algorithm"] != "ed25519"
        or signature["key_id"] != KEY_ID
        or not isinstance(signature["value"], str)
    ):
        raise UpdateError("SIGNATURE_INVALID", "missing or untrusted update signature")
    payload = {key: value for key, value in document.items() if key != "signature"}
    _validate_payload(payload)
    try:
        canonical = canonical_payload(payload)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise UpdateError("MANIFEST_INVALID", "manifest is not canonical JSON data") from exc
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        key.verify(base64.b64decode(signature["value"], validate=True), canonical)
    except Exception as exc:
        raise UpdateError("SIGNATURE_INVALID", "update manifest signature verification failed") from exc
    return payload
