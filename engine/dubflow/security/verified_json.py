"""Read a bounded JSON object from the exact bytes verified by its digest.

The caller owns path authorization, the trusted digest, the size budget and
any schema/provenance checks. This module never changes an artifact or grants
permission to reuse a checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, NoReturn


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_CHUNK_BYTES = 64 * 1024


class VerifiedJsonError(ValueError):
    """A predictable failure with a stable code and suppressed cause details.

    Formatted tracebacks retain normal implementation stack locations, while
    the public boundary suppresses chained artifact paths and error contents.
    """

    def __init__(self, code: str, condition: str) -> None:
        self.code = code
        super().__init__(condition)


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("non-JSON numeric constant")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON number is not finite")
    return number


def read_verified_json(
    path: Path | str, *, expected_sha256: str, max_bytes: int
) -> dict[str, Any]:
    """Verify a trusted ``sha256:<hex>`` digest before parsing a JSON object.

    At most ``max_bytes + 1`` bytes are consumed through one binary handle;
    the extra byte detects oversize even if the file grows. Parsing uses the
    captured bytes, without reopening the path. Digest verification alone
    proves neither the artifact's schema nor its producer/input identity.
    Non-JSON numeric constants and float overflow are INVALID_JSON failures.
    Duplicate keys and escaped surrogates retain standard decoder behavior.
    """

    if not isinstance(expected_sha256, str) or not _DIGEST.fullmatch(expected_sha256):
        raise VerifiedJsonError("INVALID_DIGEST", "expected SHA-256 must use sha256: and 64 lowercase hex digits") from None
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise VerifiedJsonError("INVALID_ARGUMENT", "max_bytes must be a positive integer") from None
    try:
        artifact_path = Path(path)
    except (TypeError, ValueError):
        raise VerifiedJsonError("INVALID_ARGUMENT", "path must be a filesystem path") from None

    payload = bytearray()
    digest = hashlib.sha256()
    try:
        with artifact_path.open("rb") as handle:
            while True:
                chunk = handle.read(min(_CHUNK_BYTES, max_bytes + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise VerifiedJsonError("TOO_LARGE", "JSON artifact exceeds max_bytes") from None
                digest.update(chunk)
    except VerifiedJsonError:
        raise
    except (OSError, ValueError):
        raise VerifiedJsonError("READ_FAILED", "unable to read JSON artifact") from None

    if "sha256:" + digest.hexdigest() != expected_sha256:
        raise VerifiedJsonError("DIGEST_MISMATCH", "JSON artifact does not match its trusted digest") from None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise VerifiedJsonError("INVALID_ENCODING", "JSON artifact must be UTF-8") from None
    try:
        document = json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, RecursionError, OverflowError):
        raise VerifiedJsonError("INVALID_JSON", "JSON artifact cannot be parsed") from None
    if not isinstance(document, dict):
        raise VerifiedJsonError("INVALID_ROOT", "JSON artifact must have an object root") from None
    return document
