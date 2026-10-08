"""Windows-user protected provider sessions; never durable plaintext cookies."""

from __future__ import annotations

import base64
import json
from http.cookies import SimpleCookie
import os
from pathlib import Path
import time
from typing import Callable, Mapping, Protocol

from .materializer import _reject_links, _write_receipt
from .source_adapter import SourceError, SourceErrorCode

PROVIDERS = frozenset({"bilibili", "douyin"})
MAX_SESSION_FILE = 32 * 1024
MAX_HEADERS_BYTES = 2048
MAX_SESSION_SECONDS = 24 * 60 * 60


def _auth_failure(provider: str | None = None) -> SourceError:
    return SourceError(SourceErrorCode.AUTH_REQUIRED, "protected provider session is missing, expired or unavailable", provider_id=provider, action="authenticate")


def validated_session_headers(headers: Mapping[str, str], *, provider: str | None = None) -> dict[str, str]:
    if not isinstance(headers, Mapping) or len(headers) != 1:
        raise _auth_failure(provider)
    result = {}
    for name, value in headers.items():
        canonical = "Cookie" if isinstance(name, str) and name.lower() == "cookie" else None
        if canonical is None or canonical in result or not isinstance(value, str) or not value or any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
            raise _auth_failure(provider)
        result[canonical] = value
    if sum(len(name) + len(value) for name, value in result.items()) > MAX_HEADERS_BYTES:
        raise _auth_failure(provider)
    try:
        parsed = SimpleCookie()
        parsed.load(result["Cookie"])
        if not 1 <= len(parsed) <= 64 or any(not item.value or any(item.values()) for item in parsed.values()):
            raise ValueError("invalid cookie scope")
    except Exception:
        raise _auth_failure(provider) from None
    return result


class SessionProtector(Protocol):
    def protect(self, data: bytes, context: bytes) -> bytes: ...
    def unprotect(self, data: bytes, context: bytes) -> bytes: ...


class WindowsUserDpapi:
    """Current-user DPAPI with UI forbidden and explicit provider entropy."""

    def __init__(self):
        if os.name != "nt":
            raise _auth_failure()
        import ctypes
        self.ctypes = ctypes

        class Blob(ctypes.Structure):
            _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        self.Blob = Blob
        self.crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        common = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
        for function in (self.crypt32.CryptProtectData, self.crypt32.CryptUnprotectData):
            function.argtypes = common
            function.restype = ctypes.c_int
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.LocalFree.restype = ctypes.c_void_p

    def _transform(self, data: bytes, context: bytes, *, decrypt: bool) -> bytes:
        if not data or len(data) > MAX_SESSION_FILE or not context or len(context) > 256:
            raise _auth_failure()
        ctypes, Blob = self.ctypes, self.Blob
        input_buffer = ctypes.create_string_buffer(data)
        context_buffer = ctypes.create_string_buffer(context)
        input_blob = Blob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        context_blob = Blob(len(context), ctypes.cast(context_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        function = self.crypt32.CryptUnprotectData if decrypt else self.crypt32.CryptProtectData
        try:
            # CRYPTPROTECT_UI_FORBIDDEN; never CRYPTPROTECT_LOCAL_MACHINE.
            if not function(ctypes.byref(input_blob), None, ctypes.byref(context_blob), None, None, 1, ctypes.byref(output)):
                raise _auth_failure()
            if not output.data or not 0 < output.size <= MAX_SESSION_FILE:
                raise _auth_failure()
            return ctypes.string_at(output.data, output.size)
        finally:
            if output.data:
                self.kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))

    def protect(self, data: bytes, context: bytes) -> bytes:
        return self._transform(data, context, decrypt=False)

    def unprotect(self, data: bytes, context: bytes) -> bytes:
        return self._transform(data, context, decrypt=True)


class ProtectedSessionBridge:
    """Adapter capability: decrypted headers exist only for the immediate call."""

    def __init__(self, root: str | Path, *, protector: SessionProtector | None = None, clock: Callable[[], float] = time.time):
        root = Path(root)
        if not root.is_absolute():
            raise _auth_failure()
        _reject_links(root)
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.protector = protector if protector is not None else WindowsUserDpapi()
        self.clock = clock

    def _path(self, provider: str) -> Path:
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise _auth_failure()
        path = self.root / (provider + ".session.json")
        _reject_links(path)
        return path

    @staticmethod
    def _context(provider: str) -> bytes:
        return ("DubFlow.source-session.v1:" + provider).encode("ascii")

    def save(self, provider: str, headers: Mapping[str, str], *, expires_at: int) -> None:
        path = self._path(provider)
        values = validated_session_headers(headers, provider=provider)
        now = self.clock()
        if type(expires_at) is not int or not now < expires_at <= now + MAX_SESSION_SECONDS:
            raise _auth_failure(provider)
        plaintext = json.dumps({"schema_version": 1, "provider_id": provider, "expires_at": expires_at, "headers": values}, sort_keys=True).encode("utf-8")
        try:
            ciphertext = self.protector.protect(plaintext, self._context(provider))
        except Exception:
            raise _auth_failure(provider) from None
        record = {"schema_version": 1, "ciphertext": base64.b64encode(ciphertext).decode("ascii")}
        if len(json.dumps(record)) > MAX_SESSION_FILE:
            raise _auth_failure(provider)
        _write_receipt(path, record)

    def get_opaque_headers(self, provider_id: str) -> dict[str, str]:
        path = self._path(provider_id)
        try:
            if not path.is_file() or path.stat().st_size > MAX_SESSION_FILE:
                raise ValueError("invalid record")
            with path.open("rb") as stream:
                encoded = stream.read(MAX_SESSION_FILE + 1)
            if len(encoded) > MAX_SESSION_FILE:
                raise ValueError("invalid record size")
            record = json.loads(encoded)
            if not isinstance(record, dict) or record.get("schema_version") != 1 or not isinstance(record.get("ciphertext"), str):
                raise ValueError("invalid record schema")
            ciphertext = base64.b64decode(record["ciphertext"], validate=True)
            plaintext = self.protector.unprotect(ciphertext, self._context(provider_id))
            if len(plaintext) > MAX_SESSION_FILE:
                raise ValueError("invalid plaintext size")
            payload = json.loads(plaintext)
            now = self.clock()
            if (not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("provider_id") != provider_id
                or type(payload.get("expires_at")) is not int or not now < payload["expires_at"] <= now + MAX_SESSION_SECONDS):
                raise ValueError("invalid session binding or expiry")
            return validated_session_headers(payload.get("headers"), provider=provider_id)
        except Exception:
            # Protector failures and malformed records never echo plaintext
            # or an underlying library's diagnostic across the adapter.
            raise _auth_failure(provider_id) from None

    def clear(self, provider: str) -> None:
        self._path(provider).unlink(missing_ok=True)
