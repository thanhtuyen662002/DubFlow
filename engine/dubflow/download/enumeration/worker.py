"""Owned source page producer over unchanged worker protocol v1.

Packets are bounded private worker artifacts, not durable queue commits. The
supervisor must validate their hash, original dispatch and producer binding
before its checked SQLite transaction. This process never receives a database.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import uuid
from urllib.parse import parse_qsl, urlsplit


if __name__ == "__main__":
    # -I -S excludes checkout paths, user packages and site customization.
    # The only additional paths are derived from this owned bundle's layout.
    _app = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(_app))
    _site = _app.parent / "runtime/Lib/site-packages"
    if _site.is_dir():
        sys.path.append(str(_site))  # Required for signed bundle verification.

from packaging.release.bootstrap import verify_bundle
from packaging.release.manifest import ReleaseManifest
from engine.dubflow.download.materializer import _reject_links
from engine.dubflow.download.runtime import provider_from_verified_bundle
from engine.dubflow.download.source_adapter import SOURCE_CONTRACT_VERSION, SourceError, SourceErrorCode, SourcePage
from engine.dubflow.download.generic.sdk import source_url
from engine.dubflow.download.enumeration.sdk import channel_url
from engine.dubflow.worker.protocol import Envelope, MAX_LINE_BYTES, MessageType, StreamValidator


STAGE_ID = "source-enumeration"
RECIPE = "owned-source-page-worker-v1"
MAX_MANIFEST = 16 * 1024 * 1024
MAX_PACKET = 4 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_PREPARE = {"bundle_root", "work_root", "manifest_sha256", "provider_id", "source_ref", "page_size"}
_PAGE = {"producer_fingerprint", "dispatch_revision", "cursor"}
_PRIVATE_QUERY = {"x_amz_credential", "x_amz_security_token", "x_amz_signature",
    "x_goog_credential", "x_goog_signature", "awsaccesskeyid", "client_secret", "oauth_token", "oauth_verifier"}


def _invalid() -> SourceError:
    return SourceError(SourceErrorCode.INVALID_INPUT, "source worker request is invalid")


def _keys(args, expected) -> None:
    if type(args) is not dict or set(args) != expected:
        raise _invalid()


def _digest(value) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise _invalid()
    return value


def _owned_interpreter(root: Path) -> None:
    runtime = root / "runtime"
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
            or Path(sys.executable).resolve() != (runtime / "python.exe").resolve()
            or Path(sys.prefix).resolve() != runtime.resolve()
            or Path(sys.base_prefix).resolve() != runtime.resolve()
            or Path(__file__).resolve() != (root / "app/engine/dubflow/download/enumeration/worker.py").resolve()):
        raise SourceError(SourceErrorCode.UNSUPPORTED, "source worker requires its verified isolated owned runtime", action="repair_runtime")


def _absolute(value) -> Path:
    if not isinstance(value, str) or not value or len(value) > 32768 or any(ord(ch) < 32 for ch in value):
        raise _invalid()
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise _invalid()
    _reject_links(path)
    return path.resolve()


def _public_reference(value: str) -> str:
    url = source_url(value)
    if any(key.casefold().replace("-", "_") in _PRIVATE_QUERY for key, _ in parse_qsl(urlsplit(url).query)):
        raise SourceError(SourceErrorCode.INVALID_INPUT, "source worker requires a public reference without credentials")
    return url


class SourceSession:
    """Verify once at admission; each page keeps this exact producer/request.

    No constructor/factory accepts fixture adapters, new inventory pins or a
    mutable model directory. Credentials and media candidates stay outside the
    producer identity and the durable page packet.
    """

    def __init__(self, args: dict):
        _keys(args, _PREPARE)
        provider = args["provider_id"]
        if not isinstance(provider, str) or provider not in {"bilibili", "douyin", "generic"}:
            raise _invalid()
        if type(args["page_size"]) is not int or not 1 <= args["page_size"] <= 100:
            raise _invalid()
        reference = _public_reference(args["source_ref"]) if provider == "generic" else channel_url(provider, args["source_ref"])
        root, work = _absolute(args["bundle_root"]), _absolute(args["work_root"])
        if work == root or root in work.parents:
            raise _invalid()
        _owned_interpreter(root)
        manifest_path = root / "release-manifest.json"
        _reject_links(manifest_path)
        with manifest_path.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST + 1)
        manifest_hash = _digest(args["manifest_sha256"])
        if len(raw) > MAX_MANIFEST or sha256(raw).hexdigest() != manifest_hash:
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "source release manifest differs from admission pins", action="repair_runtime")
        manifest = ReleaseManifest.from_mapping(json.loads(raw))
        verify_bundle(root, manifest)  # Signature policy, links, inventory equality and every file hash.
        artifacts = [item.to_dict() for item in manifest.artifacts]
        self.adapter = provider_from_verified_bundle(root, artifacts=artifacts, provider_id=provider)
        self.work_root, self.root = work, root
        self.provider, self.reference, self.page_size = provider, reference, args["page_size"]
        self.identity = {"recipe": RECIPE, "source_contract_version": SOURCE_CONTRACT_VERSION,
            "manifest_sha256": manifest_hash, "source_sha": manifest.source_sha,
            "release_version": manifest.version, "provider_id": provider,
            "source_ref": reference, "page_size": self.page_size}
        self.fingerprint = sha256(json.dumps(self.identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        self.last_revision: int | None = None
        self.completed = False

    def ready(self) -> dict:
        return {"schema_version": 1, "kind": "source-ready", "producer": dict(self.identity),
            "producer_fingerprint": self.fingerprint}

    def page(self, args: dict) -> dict:
        _keys(args, _PAGE)
        if self.completed:
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "source producer already completed")
        if _digest(args["producer_fingerprint"]) != self.fingerprint:
            raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "source producer binding changed")
        revision, cursor = args["dispatch_revision"], args["cursor"]
        if (type(revision) is not int or not 0 <= revision < (1 << 63) - 1
                or (self.last_revision is not None and revision <= self.last_revision)
                or (cursor is not None and (not isinstance(cursor, str) or not cursor
                    or len(cursor.encode("utf-8")) > 1024 or any(ord(ch) < 32 for ch in cursor)))):
            raise _invalid()
        # A fresh producer dispatch may retry only through the supervisor's new
        # generation/changed-condition decision. Never replay one generation.
        self.last_revision = revision
        page = self.adapter.enumerate_channel(self.reference, cursor=cursor, page_size=self.page_size)
        if (not isinstance(page, SourcePage) or len(page.items) + len(page.failures) > self.page_size
                or (not page.completed and page.next_cursor == cursor)):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "source page is malformed or made no progress")
        items = []
        for item in page.items:
            if item.identity.provider_id != self.provider:
                raise SourceError(SourceErrorCode.SOURCE_CHANGED, "source page provider changed")
            # Do not persist signed CDN locators, headers or credential-bearing
            # candidates from an inspected item. Reinspect its public identity
            # through the same adapter before materialization.
            public = _public_reference(item.identity.canonical_url)
            items.append({"schema_version": SOURCE_CONTRACT_VERSION,
                "identity": {**item.identity.to_dict(), "canonical_url": public},
                "title": item.title, "duration_ticks": None if item.duration_ticks is None else str(item.duration_ticks)})
        failures = [{"source_id": failure.source_id, "code": failure.code.value,
            "condition": "source item unavailable (" + failure.code.value + ")", "retryable": failure.retryable}
            for failure in page.failures]
        self.completed = page.completed
        return {"schema_version": 1, "kind": "source-page", "producer_fingerprint": self.fingerprint,
            "dispatch_revision": revision, "request_cursor": cursor,
            "page": {"schema_version": SOURCE_CONTRACT_VERSION, "items": items,
                "failures": failures, "next_cursor": page.next_cursor, "completed": page.completed}}

    def publish(self, document: dict, job_id: str) -> tuple[str, str]:
        raw = json.dumps({**document, "job_id": job_id, "stage_id": STAGE_ID},
            sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_PACKET:
            raise SourceError(SourceErrorCode.UNSUPPORTED, "source page packet exceeds its budget")
        _reject_links(self.work_root)
        filename = "source-packet-" + uuid.uuid4().hex + ".json"
        target = self.work_root / filename
        temporary = target.with_suffix(".partial")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise SourceError(SourceErrorCode.CHECKPOINT_INVALID, "source packet identity already exists")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return filename, "sha256:" + sha256(raw).hexdigest()


class _Emitter:
    def __init__(self, job_id, stdout):
        self.job_id, self.stdout = job_id, stdout
        self.sequence, self.terminal = 1, False
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.started = time.monotonic()

    def send(self, kind, payload):
        with self.lock:
            if self.terminal:
                return
            message = Envelope.from_dict({"schema_version": 1, "message_type": kind.value,
                "message_id": "source-" + uuid.uuid4().hex, "job_id": self.job_id,
                "stage_id": STAGE_ID, "sequence": self.sequence, "payload": payload})
            self.stdout.write(message.to_line())
            self.stdout.flush()
            self.sequence += 1
            if kind is MessageType.SHUTDOWN:
                self.terminal = True
                self.finished.set()

    def checkpoint(self, packet):
        self.send(MessageType.CHECKPOINT, {"checkpoint_id": packet[0], "artifact_hash": packet[1], "reusable": True})

    def heartbeat(self):
        while not self.finished.wait(5):
            self.send(MessageType.HEARTBEAT, {"monotonic_ms": int((time.monotonic() - self.started) * 1000)})

    def fail(self, code, *, retryable=False):
        # Exception messages, provider diagnostics, paths and URLs are not logs.
        self.send(MessageType.FAILURE, {"code": code, "retryable": retryable, "attempt": 1,
            "condition": "source worker stopped (" + code + ")"})
        self.send(MessageType.SHUTDOWN, {"status": "failed"})


def _request(line, validator, job_id=None):
    message = Envelope.from_line(line)
    validator.accept(message)
    if (message.stage_id != STAGE_ID or (job_id is not None and message.job_id != job_id)
            or message.message_type not in {MessageType.COMMAND, MessageType.CANCEL}):
        raise _invalid()
    return message


def main() -> int:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    validator = StreamValidator()
    emitter = None
    try:
        first = _request(stdin.readline(MAX_LINE_BYTES + 1), validator)
        if first.message_type is not MessageType.COMMAND or first.payload["command"] != "source_prepare":
            raise _invalid()
        emitter = _Emitter(first.job_id, stdout)
        pending = queue.Queue(maxsize=4)

        def read_controls():
            try:
                while not emitter.finished.is_set():
                    line = stdin.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        # Parent death/EOF is not graceful completion. Immediate
                        # exit closes nested source Job handles and private stdio;
                        # the supervisor retains its last committed page.
                        if not emitter.finished.is_set():
                            os._exit(2)
                        return
                    request = _request(line, validator, first.job_id)
                    if request.message_type is MessageType.CANCEL:
                        # Only the supervisor knows which emitted pages committed.
                        # Never treat the producer's latest packet as durable.
                        emitter.send(MessageType.CHECKPOINT, {"checkpoint_id": "source-last-supervisor-commit",
                            "reusable": True})
                        emitter.send(MessageType.SHUTDOWN, {"status": "cancelled"})
                        os._exit(0)
                    if request.payload["command"] != "source_page":
                        raise _invalid()
                    pending.put_nowait(request)
            except Exception:
                emitter.fail("SOURCE_WORKER_REQUEST_INVALID")
                os._exit(2)

        threading.Thread(target=read_controls, daemon=True).start()
        threading.Thread(target=emitter.heartbeat, daemon=True).start()
        session = SourceSession(first.payload["args"])
        emitter.checkpoint(session.publish(session.ready(), first.job_id))
        while not emitter.finished.is_set():
            request = pending.get()
            document = session.page(request.payload["args"])
            emitter.checkpoint(session.publish(document, first.job_id))
            if document["page"]["completed"]:
                emitter.send(MessageType.SHUTDOWN, {"status": "completed"})
                return 0
        return 0
    except Exception as error:
        if emitter is not None:
            emitter.fail(error.code.value if isinstance(error, SourceError) else "SOURCE_WORKER_FAILED",
                retryable=error.retryable if isinstance(error, SourceError) else False)
        return 2
    finally:
        if emitter is not None:
            emitter.finished.set()


if __name__ == "__main__":
    raise SystemExit(main())
