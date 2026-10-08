"""Pinned app-runtime helper for a provider-scoped in-memory cookie jar."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Mapping

from .generic import SubprocessYtDlpRunner
from .materializer import _reject_links
from .sessions import validated_session_headers
from .source_adapter import SourceError, SourceErrorCode


class AuthenticatedYtDlpTransport:
    def __init__(self, *, runtime_root: str | Path, python: str | Path, helper: str | Path,
                 sdk_archive: str | Path, pins: Mapping[str, str], timeout_s: float = 180):
        self.root = Path(runtime_root)
        self.python, self.helper, self.sdk_archive = Path(python), Path(helper), Path(sdk_archive)
        self.paths = {"python": self.python, "helper": self.helper, "sdk_archive": self.sdk_archive}
        self.pins = dict(pins)
        self.timeout_s = timeout_s
        if not 0 < timeout_s <= 1800 or set(self.pins) != set(self.paths):
            raise ValueError("approved runtime/helper/SDK pins and bounded timeout are required")
        self.runner = SubprocessYtDlpRunner()
        self._verify()

    def _verify(self):
        try:
            if not self.root.is_absolute() or not self.root.is_dir():
                raise ValueError("invalid runtime")
            _reject_links(self.root)
            for key, path in self.paths.items():
                _reject_links(path)
                pin = self.pins[key]
                if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin) or not path.is_absolute() or not path.is_file():
                    raise ValueError("missing producer pin")
                path.resolve().relative_to(self.root.resolve())
                before = path.stat()
                maximum = 1024 * 1024 if key == "helper" else 256 * 1024 * 1024
                if not 0 < before.st_size <= maximum:
                    raise ValueError("producer exceeds size budget")
                digest, count = hashlib.sha256(), 0
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(64 * 1024), b""):
                        count += len(chunk)
                        if count > maximum:
                            raise ValueError("producer grew")
                        digest.update(chunk)
                after = path.stat()
                if digest.hexdigest() != pin or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("producer mismatch")
        except Exception as error:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "authenticated extractor runtime verification failed", action="repair_runtime") from error

    def inspect_url(self, url: str, *, provider_id: str, headers: Mapping[str, str] | None = None):
        request = {"schema_version": 1, "provider_id": provider_id, "url": url,
                   "headers": validated_session_headers(headers, provider=provider_id) if headers else {}}
        return self._invoke(request, provider_id=provider_id)

    def health_check(self, *, expected_sdk_version: str):
        result = self._invoke({"schema_version": 1, "operation": "health_check"})
        try:
            if (result.get("schema_version") != 1 or result.get("sdk_version") != expected_sdk_version
                or result.get("isolated") is not True or result.get("no_site") is not True
                or Path(result["python_executable"]).resolve() != self.python.resolve()):
                raise ValueError("native producer health mismatch")
        except (KeyError, TypeError, ValueError):
            raise SourceError(SourceErrorCode.UNSUPPORTED, "source runtime health identity mismatch", action="repair_runtime") from None
        return result

    def _invoke(self, request: dict, *, provider_id: str | None = None):
        self._verify()
        payload = json.dumps(request, ensure_ascii=True).encode("utf-8")
        code, stdout, stderr = self.runner.run_with_input(
            [str(self.python), "-I", "-S", "-B", str(self.helper), str(self.sdk_archive)],
            timeout_s=self.timeout_s, stdin_bytes=payload,
        )
        if code:
            codes = {2: SourceErrorCode.AUTH_REQUIRED, 3: SourceErrorCode.RATE_LIMITED,
                     4: SourceErrorCode.NOT_FOUND, 5: SourceErrorCode.SOURCE_CHANGED}
            error_code = codes.get(code, SourceErrorCode.NETWORK)
            raise SourceError(error_code, "authenticated provider inspection failed", provider_id=provider_id,
                retryable=code in {3, 6}, action="authenticate" if code == 2 else "retry_with_changed_conditions" if code in {3, 6} else None)
        try:
            value = json.loads(stdout)
            if not isinstance(value, dict):
                raise ValueError("invalid metadata")
            return value
        except (ValueError, TypeError) as error:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "authenticated extractor returned invalid metadata", provider_id=provider_id) from error
