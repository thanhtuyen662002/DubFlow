"""Actual installed native/owned-worker session boundary, synthetic cookies only.

No network, browser extraction, provider login or production-quality claim.
The bundle is never modified; all state belongs to a disposable data profile.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
import uuid

MAX_LINE = 64 * 1024
DEADLINE = 180
CASES = ("native_save_both_providers", "native_owner_reopen", "owned_factory_capability",
         "provider_entropy_refusal", "ciphertext_tamper_refusal", "native_clear_both_providers",
         "generic_session_refusal", "malformed_session_refusal", "plaintext_absent")


def _failure():
    # Never include raw requests, subprocess diagnostics or credential values.
    return ValueError("native protected-session qualification failed")


def _event(raw: bytes, secret: str) -> dict:
    if not raw or len(raw) > MAX_LINE or not raw.endswith(b"\n") or secret.split("=")[-1].encode() in raw:
        raise _failure()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise _failure() from None
    if not isinstance(value, dict) or not isinstance(value.get("event"), str):
        raise _failure()
    if set(value) & {"headers", "cookie", "Cookie", "ciphertext", "expires_at"}:
        raise _failure()
    return value


class _Native:
    def __init__(self, root: Path, data: Path, manifest_sha256: str, secret: str):
        self.secret = secret
        self.output = queue.Queue(maxsize=16)
        self.stopping = threading.Event()
        self.stderr_secret = False
        self.frames = 0
        self.process = subprocess.Popen([str(root / "app/bin/dubflow-supervisor.exe"), "source-serve",
            "--root", str(root), "--data-root", str(data), "--manifest-sha256", manifest_sha256],
            cwd=data, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.diagnostics = threading.Thread(target=self._stderr, daemon=True)
        self.reader.start()
        self.diagnostics.start()
        try:
            if self.receive().get("event") != "source_ready":
                raise _failure()
        except Exception:
            self.close(force=True)
            raise _failure() from None

    def _read(self):
        try:
            while not self.stopping.is_set():
                raw = self.process.stdout.readline(MAX_LINE + 1)
                if not raw:
                    raise _failure()
                self.frames += 1
                if self.frames > 128:
                    raise _failure()
                self.output.put_nowait(_event(raw, self.secret))
        except Exception:
            try:
                self.output.put_nowait(None)
            except queue.Full:
                pass

    def _stderr(self):
        suffix = b""
        while True:
            raw = self.process.stderr.read(4096)
            if not raw:
                return
            combined = suffix + raw
            if self.secret.split("=")[-1].encode() in combined:
                self.stderr_secret = True
            suffix = combined[-len(self.secret):]

    def receive(self):
        try:
            event = self.output.get(timeout=DEADLINE)
        except queue.Empty:
            raise _failure() from None
        if event is None:
            raise _failure()
        return event

    def send(self, request: dict):
        raw = json.dumps(request, ensure_ascii=True, allow_nan=False).encode() + b"\n"
        if len(raw) > MAX_LINE:
            raise _failure()
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def operation(self, provider: str, operation: str, state: str, **extra):
        self.send({"command": "session_" + operation, "provider_id": provider, **extra})
        preparing = self.receive()
        if preparing != {"event": "source_session_preparing", "provider_id": provider, "operation": operation}:
            raise _failure()
        expected = {"event": "source_session", "provider_id": provider, "operation": operation, "state": state}
        if self.receive() != expected:
            raise _failure()

    def refused(self, request: dict):
        self.send(request)
        if self.receive() != {"event": "source_error", "code": "SOURCE_REQUEST_REJECTED", "retryable": False}:
            raise _failure()

    def close(self, *, force=False):
        try:
            if not force:
                self.send({"command": "shutdown"})
                if self.receive() != {"event": "source_shutdown"}:
                    raise _failure()
                self.process.stdin.close()
                if self.process.wait(timeout=8) != 0:
                    raise _failure()
        finally:
            self.stopping.set()
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=8)
            self.reader.join(timeout=2)
            self.diagnostics.join(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()
        if not force and (self.stderr_secret or self.reader.is_alive() or self.diagnostics.is_alive()):
            raise _failure()


_FACTORY_PROBE = r'''
import json,pathlib,sys
args=json.loads(sys.stdin.buffer.read())
root=pathlib.Path(args.pop('root'))
expected=args.pop('expected')
sys.path.insert(0,str(root/'app'))
sys.path.append(str(root/'runtime/Lib/site-packages'))
from engine.dubflow.download.enumeration.worker import SourceSession
session=SourceSession(args)
bridge=session.adapter._session_bridge
if bridge is None or bridge.get_opaque_headers(session.provider)!={'Cookie':expected}:
 raise SystemExit(2)
raw=json.dumps(session.ready(),sort_keys=True)
if expected.split('=')[-1] in raw or 'headers' in raw or 'data_root' in raw:
 raise SystemExit(2)
print(json.dumps({'status':'passed','provider_id':session.provider,'producer_fingerprint':session.fingerprint}))
'''


def _owned_factory(root: Path, data: Path, manifest_hash: str, secret: str):
    env = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    for provider in ("bilibili", "douyin"):
        work = data / "source-work" / ("factory-" + provider)
        work.mkdir(parents=True)
        args = {"root": str(root), "expected": secret, "bundle_root": str(root), "data_root": str(data),
                "work_root": str(work), "manifest_sha256": manifest_hash, "provider_id": provider,
                "source_ref": "123", "page_size": 1}
        # Synthetic cookie enters only the private control pipe. The actual
        # installed SourceSession verifies its origin and full tree and factory.
        process = subprocess.Popen([str(root / "runtime/python.exe"), "-I", "-S", "-B", "-c", _FACTORY_PROBE],
            cwd=data, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            output, diagnostics = process.communicate(json.dumps(args).encode(), timeout=DEADLINE)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise _failure() from None
        if process.returncode or len(output) > 4096 or secret.split("=")[-1].encode() in output + diagnostics:
            raise _failure()
        try:
            receipt = json.loads(output)
        except ValueError:
            raise _failure() from None
        if receipt.get("status") != "passed" or receipt.get("provider_id") != provider:
            raise _failure()


def qualify_source_sessions(root: Path, manifest_sha256: str, expected_source_sha: str) -> dict:
    if os.name != "nt":
        raise ValueError("Windows native session qualification requires Windows")
    root = root.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) or not re.fullmatch(r"[0-9a-f]{40}", expected_source_sha):
        raise _failure()
    with (root / "release-manifest.json").open("rb") as stream:
        manifest_raw = stream.read(16 * 1024 * 1024 + 1)
    if (len(manifest_raw) > 16 * 1024 * 1024 or hashlib.sha256(manifest_raw).hexdigest() != manifest_sha256
            or json.loads(manifest_raw).get("source_sha") != expected_source_sha):
        raise _failure()
    started = time.monotonic()
    secret = "dubflow_qa_sid=" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="dubflow-native-session-") as directory:
        data = Path(directory).resolve()
        if data == root or root in data.parents:
            raise _failure()
        native = None
        try:
            native = _Native(root, data, manifest_sha256, secret)
            for provider in ("bilibili", "douyin"):
                native.operation(provider, "status", "missing")
                native.operation(provider, "save", "ready", headers={"Cookie": secret}, expires_at=int(time.time()) + 3600)
            for path in data.rglob("*"):
                if path.is_file() and (path.stat().st_size > 4 * 1024 * 1024 or secret.split("=")[-1].encode() in path.read_bytes()):
                    raise _failure()
            native.close()
            native = None
            _owned_factory(root, data, manifest_sha256, secret)
            native = _Native(root, data, manifest_sha256, secret)
            for provider in ("bilibili", "douyin"):
                native.operation(provider, "status", "ready")
            protected = data / "control/source-sessions"
            bili, douyin = protected / "bilibili.session.json", protected / "douyin.session.json"
            original = bili.read_bytes()
            record = json.loads(original)
            if set(record) != {"schema_version", "ciphertext"} or record["schema_version"] != 1:
                raise _failure()
            douyin.write_bytes(original)
            native.operation("douyin", "status", "expired_or_unavailable")
            encrypted = bytearray(base64.b64decode(record["ciphertext"], validate=True))
            encrypted[len(encrypted) // 2] ^= 1
            record["ciphertext"] = base64.b64encode(encrypted).decode()
            bili.write_text(json.dumps(record), encoding="utf-8")
            native.operation("bilibili", "status", "expired_or_unavailable")
            native.refused({"command": "session_save", "provider_id": "generic", "headers": {"Cookie": secret}, "expires_at": int(time.time()) + 3600})
            native.refused({"command": "session_save", "provider_id": "bilibili", "headers": {"Authorization": secret}, "expires_at": int(time.time()) + 3600})
            native.refused({"command": "session_save", "provider_id": "bilibili", "headers": {"Cookie": secret}, "expires_at": 1})
            native.refused({"command": "session_status", "provider_id": "bilibili", "headers": {"Cookie": secret}})
            for provider in ("bilibili", "douyin"):
                native.operation(provider, "clear", "missing")
                native.operation(provider, "status", "missing")
            native.close()
            native = None
            for path in data.rglob("*"):
                if path.is_file() and (path.stat().st_size > 4 * 1024 * 1024 or secret.split("=")[-1].encode() in path.read_bytes()):
                    raise _failure()
            if list(protected.glob("*.session.json")):
                raise _failure()
        finally:
            if native is not None:
                native.close(force=True)
    return {"status": "passed", "scope": "native-windows-protected-session-boundary",
            "source_sha": expected_source_sha, "manifest_sha256": manifest_sha256,
            "supervisor_sha256": hashlib.sha256((root / "app/bin/dubflow-supervisor.exe").read_bytes()).hexdigest(),
            "cases": list(CASES), "elapsed_seconds": round(time.monotonic() - started, 3),
            "synthetic_credentials_only": True, "network_requests": 0,
            "browser_extraction": "not_run", "live_authenticated_provider": "not_run",
            "production_qualified": False}
