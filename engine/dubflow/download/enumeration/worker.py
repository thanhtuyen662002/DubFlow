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
from engine.dubflow.download.materializer import DownloadResult, MAX_DOWNLOAD_BYTES, _reject_links
from engine.dubflow.download.runtime import provider_from_verified_bundle
from engine.dubflow.download.sessions import ProtectedSessionBridge, PROVIDERS
from engine.dubflow.download.source_adapter import SOURCE_CONTRACT_VERSION, SourceError, SourceErrorCode, SourceItem, SourcePage
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
_DOWNLOAD = {"producer_fingerprint", "dispatch_revision", "identity_key", "source_id", "source_url", "resume"}
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


def _verified_runtime(args: dict) -> tuple[Path, Path, ReleaseManifest, str]:
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
    verify_bundle(root, manifest)
    return root, work, manifest, manifest_hash


def _session_root(args: dict, root: Path, work: Path) -> Path:
    # The native owner admits data_root; no caller-selected credential filename.
    data = _absolute(args["data_root"])
    if data == root or root in data.parents or data == work or data not in work.parents:
        raise _invalid()
    protected = data / "control/source-sessions"
    if protected == root or root in protected.parents:
        raise _invalid()
    _reject_links(protected)
    return protected


def _publish(work: Path, document: dict, job_id: str) -> tuple[str, str]:
    raw = json.dumps({**document, "job_id": job_id, "stage_id": STAGE_ID},
        sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_PACKET:
        raise SourceError(SourceErrorCode.UNSUPPORTED, "source packet exceeds its budget")
    _reject_links(work)
    filename = "source-packet-" + uuid.uuid4().hex + ".json"
    target = work / filename
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


class SessionOperation:
    """One private protected-session operation, with no queue/database handle."""

    def __init__(self, args: dict):
        expected = {"bundle_root", "work_root", "data_root", "manifest_sha256", "provider_id", "operation"}
        if isinstance(args, dict) and args.get("operation") == "save":
            expected |= {"headers", "expires_at"}
        _keys(args, expected)
        provider, operation = args["provider_id"], args["operation"]
        if (not isinstance(provider, str) or provider not in PROVIDERS
                or not isinstance(operation, str) or operation not in {"save", "status", "clear"}):
            raise _invalid()
        root, work, _, _ = _verified_runtime(args)
        self.work = work
        protected = _session_root(args, root, work)
        bridge = ProtectedSessionBridge(protected)
        if operation == "save":
            bridge.save(provider, args["headers"], expires_at=args["expires_at"])
            state = "ready"
        elif operation == "clear":
            bridge.clear(provider)
            state = "missing"
        else:
            path = protected / (provider + ".session.json")
            _reject_links(path)
            state = "missing"
            if path.exists():
                try:
                    bridge.get_opaque_headers(provider)
                    state = "ready"
                except SourceError as error:
                    if error.code is not SourceErrorCode.AUTH_REQUIRED:
                        raise
                    state = "expired_or_unavailable"
        self.document = {"schema_version": 1, "kind": "source-session", "provider_id": provider,
                         "operation": operation, "state": state}

    def publish(self, job_id: str) -> tuple[str, str]:
        return _publish(self.work, self.document, job_id)


class SourceSession:
    """Verify once at admission; each page keeps this exact producer/request.

    No constructor/factory accepts fixture adapters, new inventory pins or a
    mutable model directory. Credentials and media candidates stay outside the
    producer identity and the durable page packet.
    """

    def __init__(self, args: dict):
        self.single_video = isinstance(args, dict) and "source_mode" in args
        expected = _PREPARE | ({"source_mode"} if self.single_video else set())
        if isinstance(args, dict) and "data_root" in args:
            expected |= {"data_root"}
        _keys(args, expected)
        if self.single_video and args["source_mode"] != "video":
            raise _invalid()
        provider = args["provider_id"]
        if not isinstance(provider, str) or provider not in {"bilibili", "douyin", "generic"}:
            raise _invalid()
        if type(args["page_size"]) is not int or not 1 <= args["page_size"] <= 100:
            raise _invalid()
        if self.single_video and args["page_size"] != 1:
            raise _invalid()
        reference = _public_reference(args["source_ref"]) if provider == "generic" or self.single_video else channel_url(provider, args["source_ref"])
        root, work, manifest, manifest_hash = _verified_runtime(args)
        artifacts = [item.to_dict() for item in manifest.artifacts]
        options = {}
        if "data_root" in args:
            protected = _session_root(args, root, work)
            if provider in PROVIDERS:
                record = protected / (provider + ".session.json")
                _reject_links(record)
                if record.exists():
                    bridge = ProtectedSessionBridge(protected)
                    # A stale saved session is an actionable auth condition;
                    # never silently downgrade an explicit protected capability.
                    bridge.get_opaque_headers(provider)
                    options["session_bridge"] = bridge
        self.adapter = provider_from_verified_bundle(root, artifacts=artifacts, provider_id=provider, **options)
        self.work_root, self.root = work, root
        self.provider, self.reference, self.page_size = provider, reference, args["page_size"]
        self.identity = {"recipe": "owned-source-page-worker-v2" if self.single_video else RECIPE, "source_contract_version": SOURCE_CONTRACT_VERSION,
            "manifest_sha256": manifest_hash, "source_sha": manifest.source_sha,
            "release_version": manifest.version, "provider_id": provider,
            "source_ref": reference, "page_size": self.page_size}
        if self.single_video:
            self.identity["source_mode"] = "video"
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
        if self.single_video and cursor is not None:
            raise _invalid()
        self.last_revision = revision
        page = (SourcePage((self.adapter.inspect(self.reference),), None, True) if self.single_video
                else self.adapter.enumerate_channel(self.reference, cursor=cursor, page_size=self.page_size))
        if (not isinstance(page, SourcePage) or len(page.items) + len(page.failures) > self.page_size
                or (not page.completed and page.next_cursor == cursor)):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "source page is malformed or made no progress")
        items = []
        failures = [{"source_id": failure.source_id, "code": failure.code.value,
            "condition": "source item unavailable (" + failure.code.value + ")", "retryable": failure.retryable}
            for failure in page.failures]
        for item in page.items:
            if item.identity.provider_id != self.provider:
                raise SourceError(SourceErrorCode.SOURCE_CHANGED, "source page provider changed")
            # Do not persist signed CDN locators, headers or credential-bearing
            # candidates from an inspected item. Reinspect its public identity
            # through the same adapter before materialization.
            try:
                public = _public_reference(item.identity.canonical_url)
            except SourceError as error:
                failures.append({"source_id": item.identity.source_id, "code": error.code.value,
                    "condition": "source item reference rejected", "retryable": False})
                continue
            items.append({"schema_version": SOURCE_CONTRACT_VERSION,
                "identity": {**item.identity.to_dict(), "canonical_url": public},
                "title": item.title, "duration_ticks": None if item.duration_ticks is None else str(item.duration_ticks)})
        self.completed = page.completed
        return {"schema_version": 1, "kind": "source-page", "producer_fingerprint": self.fingerprint,
            "dispatch_revision": revision, "request_cursor": cursor,
            "page": {"schema_version": SOURCE_CONTRACT_VERSION, "items": items,
                "failures": failures, "next_cursor": page.next_cursor, "completed": page.completed}}

    def download(self, args: dict, *, progress=None, cancel=None) -> dict:
        """Reinspect one original identity and materialize only private media.

        Progress is observed selected-stream bytes, not a durable media receipt.
        Only the supervisor can authorize a final producer-bound queue commit.
        """
        _keys(args, _DOWNLOAD)
        revision = args["dispatch_revision"]
        if (self.completed or _digest(args["producer_fingerprint"]) != self.fingerprint
                or type(revision) is not int or not 0 <= revision < (1 << 63) - 1
                or (self.last_revision is not None and revision <= self.last_revision)
                or type(args["resume"]) is not bool):
            raise _invalid()
        for key, limit in (("identity_key", 1024), ("source_id", 512)):
            value = args[key]
            if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit
                    or any(ord(ch) < 32 for ch in value)):
                raise _invalid()
        reference = _public_reference(args["source_url"])
        self.last_revision = revision
        item = self.adapter.inspect(reference)
        if (not isinstance(item, SourceItem) or item.identity.provider_id != self.provider
                or item.identity.source_id != args["source_id"]
                or item.identity.identity_key != args["identity_key"]
                or _public_reference(item.identity.canonical_url) != reference):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "selected source identity changed before materialization")
        binding = {"producer_fingerprint": self.fingerprint, "dispatch_revision": revision,
            "identity_key": args["identity_key"], "source_id": args["source_id"], "source_url": reference}
        observed = [0, None]

        def observe(downloaded, total):
            if (type(downloaded) is not int or not 0 <= downloaded <= 2 * MAX_DOWNLOAD_BYTES
                    or (total is not None and (type(total) is not int or not downloaded <= total <= 2 * MAX_DOWNLOAD_BYTES))):
                raise _invalid()
            observed[:] = [downloaded, total]
            if progress is not None:
                progress({"schema_version": 1, "kind": "source-download-progress", **binding,
                    "downloaded_bytes": downloaded, "total_bytes": total})

        _reject_links(self.work_root)
        destination = self.work_root / "source-media.mp4"
        _reject_links(destination)
        result = self.adapter.download(item, destination, root=self.work_root, resume=args["resume"],
            cancel=cancel, progress=observe)
        if (not isinstance(result, DownloadResult) or result.path != destination
                or type(result.size_bytes) is not int or not 0 < result.size_bytes <= MAX_DOWNLOAD_BYTES
                or type(result.resumed) is not bool):
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "materialized source receipt is invalid")
        _digest(result.sha256)
        _reject_links(destination)
        if not destination.is_file() or destination.stat().st_size != result.size_bytes:
            raise SourceError(SourceErrorCode.SOURCE_CHANGED, "materialized source file differs from its receipt")
        self.completed = True
        return {"schema_version": 1, "kind": "source-download", **binding,
            "media_file": destination.name, "size_bytes": result.size_bytes, "sha256": result.sha256,
            "resumed": result.resumed, "downloaded_bytes": observed[0], "total_bytes": observed[1]}

    def publish(self, document: dict, job_id: str) -> tuple[str, str]:
        return _publish(self.work_root, document, job_id)


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
            if kind is MessageType.SHUTDOWN:
                # Once the parent sees shutdown it may close its control pipe.
                # Publish local terminal state before exposing that wire event.
                self.terminal = True
                self.finished.set()
            self.stdout.write(message.to_line())
            self.stdout.flush()
            self.sequence += 1

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
    controls = None
    try:
        first = _request(stdin.readline(MAX_LINE_BYTES + 1), validator)
        if first.message_type is not MessageType.COMMAND or first.payload["command"] not in {"source_prepare", "source_session"}:
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
                    if first.payload["command"] == "source_session" or request.payload["command"] not in {"source_page", "source_download"}:
                        raise _invalid()
                    pending.put_nowait(request)
            except Exception:
                emitter.fail("SOURCE_WORKER_REQUEST_INVALID")
                os._exit(2)

        controls = threading.Thread(target=read_controls, daemon=True)
        controls.start()
        threading.Thread(target=emitter.heartbeat, daemon=True).start()
        if first.payload["command"] == "source_session":
            operation = SessionOperation(first.payload["args"])
            emitter.checkpoint(operation.publish(first.job_id))
            emitter.send(MessageType.SHUTDOWN, {"status": "completed"})
            return 0
        session = SourceSession(first.payload["args"])
        emitter.checkpoint(session.publish(session.ready(), first.job_id))
        while not emitter.finished.is_set():
            request = pending.get()
            if request.payload["command"] == "source_download":
                last_progress = [0.0]

                def publish_progress(document):
                    now = time.monotonic()
                    # At most one progress packet per five seconds, plus the
                    # existing heartbeat; long transfers stay inside wire bounds.
                    if now - last_progress[0] >= 5:
                        emitter.checkpoint(session.publish(document, first.job_id))
                        last_progress[0] = now

                document = session.download(request.payload["args"], progress=publish_progress)
            else:
                document = session.page(request.payload["args"])
            emitter.checkpoint(session.publish(document, first.job_id))
            if document["kind"] == "source-download" or document["page"]["completed"]:
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
        if controls is not None:
            # Native closes stdin after a validated terminal message. Give the
            # blocked reader time to retire before BufferedReader finalization.
            controls.join(timeout=1)


if __name__ == "__main__":
    raise SystemExit(main())
