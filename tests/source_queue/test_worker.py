"""Boundary tests, not installed native scan/download acceptance."""
from __future__ import annotations

from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from engine.dubflow.download.enumeration import worker
from engine.dubflow.download.source_adapter import (
    MediaCandidate, SourceError, SourceErrorCode, SourceIdentity, SourceItem,
    SourcePage, SourcePageFailure, SubtitleCandidate,
)
from engine.dubflow.worker.protocol import Envelope, MessageType, StreamValidator

_REAL_OWNED_INTERPRETER = worker._owned_interpreter


class SourceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dubflow-source-worker-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "bundle"
        self.work = Path(self.directory.name) / "private-work"
        self.root.mkdir()
        self.work.mkdir()
        self.raw = b'{"fixture":"only manifest parser is substituted in these boundary tests"}'
        (self.root / "release-manifest.json").write_bytes(self.raw)
        self.args = {"bundle_root": str(self.root), "work_root": str(self.work),
            "manifest_sha256": sha256(self.raw).hexdigest(), "provider_id": "generic",
            "source_ref": "https://example.test/playlist", "page_size": 3}
        self.adapter = Mock()
        self.adapter.enumerate_channel.return_value = SourcePage((), None, True)
        self.manifest = SimpleNamespace(source_sha="a" * 40, version="0.1.0-rc.test", artifacts=[])
        self.owner = self.start_patch("_owned_interpreter", return_value=None)
        self.parser = self.start_patch("ReleaseManifest.from_mapping", return_value=self.manifest)
        self.verifier = self.start_patch("verify_bundle")
        self.factory = self.start_patch("provider_from_verified_bundle", return_value=self.adapter)

    def start_patch(self, name, **kwargs):
        patcher = patch("engine.dubflow.download.enumeration.worker." + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def session(self, **changes):
        return worker.SourceSession({**self.args, **changes})

    def dispatch(self, session, revision=1, cursor=None, **changes):
        return session.page({"producer_fingerprint": session.fingerprint,
            "dispatch_revision": revision, "cursor": cursor, **changes})

    def test_changed_manifest_and_wrong_owned_origin_fail_before_factory(self):
        with self.assertRaises(SourceError):
            self.session(manifest_sha256="b" * 64)
        self.verifier.assert_not_called()
        self.factory.assert_not_called()
        self.owner.side_effect = SourceError(SourceErrorCode.UNSUPPORTED, "owned origin required")
        with self.assertRaises(SourceError):
            self.session()
        self.verifier.assert_not_called()
        self.factory.assert_not_called()

    def test_inventory_failure_is_never_converted_into_new_pins(self):
        self.verifier.side_effect = ValueError("bundle changed")
        with self.assertRaises(ValueError):
            self.session()
        self.factory.assert_not_called()
        self.adapter.enumerate_channel.assert_not_called()

    def test_actual_origin_guard_refuses_a_different_runtime_root(self):
        with self.assertRaises(SourceError):
            _REAL_OWNED_INTERPRETER(self.root)

    def test_cloud_and_oauth_credentials_cannot_enter_producer_pins_or_packets(self):
        for key in ["X-Amz-Credential", "X-Amz-Security-Token", "X-Amz-Signature", "X-Goog-Signature",
                    "AWSAccessKeyId", "client_secret", "oauth_token"]:
            with self.subTest(key=key), self.assertRaises(SourceError):
                self.session(source_ref="https://example.test/playlist?" + key + "=private-value")
        self.factory.assert_not_called()
        session = self.session()
        self.adapter.enumerate_channel.return_value = SourcePage((SourceItem(
            SourceIdentity("generic", "one", "https://example.test/one?X-Amz-Signature=private-value"), "one"),), None, True)
        with self.assertRaises(SourceError):
            self.dispatch(session)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_work_root_inside_bundle_or_source_credentials_never_admit(self):
        inside = self.root / "private-work"
        inside.mkdir()
        for changes in [{"work_root": str(inside)}, {"work_root": str(self.root)},
                        {"source_ref": "https://example.test/playlist?token=private"},
                        {"source_ref": "https://user:private@example.test/playlist"}]:
            with self.subTest(changes=changes), self.assertRaises(SourceError):
                self.session(**changes)
        self.factory.assert_not_called()

    def test_admission_normalizes_channel_and_binds_every_request_pin(self):
        first = self.session(provider_id="bilibili", source_ref="123")
        alias = self.session(provider_id="bilibili", source_ref="https://space.bilibili.com/123/video")
        self.assertEqual(first.reference, "https://space.bilibili.com/123/video")
        self.assertEqual(first.fingerprint, alias.fingerprint)
        self.assertNotEqual(first.fingerprint, self.session(provider_id="bilibili", source_ref="124").fingerprint)
        self.assertNotEqual(first.fingerprint, self.session(provider_id="bilibili", source_ref="123", page_size=2).fingerprint)
        self.manifest.source_sha = "b" * 40
        self.assertNotEqual(first.fingerprint, self.session(provider_id="bilibili", source_ref="123").fingerprint)

    def test_changed_producer_and_malformed_dispatch_fail_before_network(self):
        session = self.session()
        for changes in [{"producer_fingerprint": "b" * 64}, {"producer_fingerprint": "A" * 64},
                        {"dispatch_revision": True}, {"dispatch_revision": -1},
                        {"dispatch_revision": (1 << 63) - 1}, {"cursor": ""}, {"cursor": "x" * 1025}]:
            with self.subTest(changes=changes), self.assertRaises(SourceError):
                self.dispatch(session, **changes)
        self.adapter.enumerate_channel.assert_not_called()

    def test_replayed_dispatch_is_rejected_but_next_revision_keeps_cursor(self):
        session = self.session()
        self.adapter.enumerate_channel.return_value = SourcePage((), "next-cursor", False)
        self.dispatch(session, revision=4, cursor="page-4")
        self.adapter.enumerate_channel.assert_called_once_with(session.reference, cursor="page-4", page_size=3)
        with self.assertRaises(SourceError):
            self.dispatch(session, revision=4, cursor="page-4")
        self.assertEqual(self.adapter.enumerate_channel.call_count, 1)
        document = self.dispatch(session, revision=5, cursor="page-4")
        self.assertEqual((document["dispatch_revision"], document["request_cursor"]), (5, "page-4"))
        self.assertEqual(document["producer_fingerprint"], session.fingerprint)

    def test_completed_producer_refuses_even_a_new_dispatch(self):
        session = self.session()
        self.dispatch(session)
        with self.assertRaises(SourceError):
            self.dispatch(session, revision=2)
        self.assertEqual(self.adapter.enumerate_channel.call_count, 1)

    def test_packets_keep_public_identity_and_drop_media_credentials(self):
        session = self.session()
        item = SourceItem(SourceIdentity("generic", "one", "https://example.test/watch/one"), "Một video", duration_ticks=90,
            media_candidates=(MediaCandidate("one", "https://cdn.test/media?token=private-media", "progressive", "video/mp4"),),
            subtitle_candidates=(SubtitleCandidate("one", "https://cdn.test/sub?sig=private-subtitle", "vi", "vtt"),))
        self.adapter.enumerate_channel.return_value = SourcePage((item,), None, True,
            (SourcePageFailure("deleted", SourceErrorCode.PRIVATE, "provider Cookie: private-error", True),))
        document = self.dispatch(session)
        encoded = json.dumps(document)
        for secret in ["private-media", "private-subtitle", "private-error", "Cookie", "media_candidates", "subtitle_candidates"]:
            self.assertNotIn(secret, encoded)
        self.assertEqual(document["page"]["items"][0]["identity"]["identity_key"], "generic:one")
        self.assertEqual(document["page"]["items"][0]["duration_ticks"], "90")
        self.assertTrue(document["page"]["failures"][0]["retryable"])

    def test_poisoned_identity_or_wrong_provider_cannot_publish(self):
        for identity in [SourceIdentity("bilibili", "one", "https://www.bilibili.com/video/one"),
                         SourceIdentity("generic", "one", "https://example.test/video?cookie=private")]:
            with self.subTest(identity=identity):
                session = self.session()
                self.adapter.enumerate_channel.return_value = SourcePage((SourceItem(identity, "one"),), None, True)
                with self.assertRaises(SourceError):
                    self.dispatch(session)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_oversized_page_and_no_progress_are_actionable(self):
        item = SourceItem(SourceIdentity("generic", "one", "https://example.test/one"), "one")
        for page in [SourcePage((item,) * 4, None, True), SourcePage((), "cursor", False)]:
            with self.subTest(page=page), self.assertRaises(SourceError):
                session = self.session()
                self.adapter.enumerate_channel.return_value = page
                self.dispatch(session, cursor="cursor")

    def test_atomic_packet_is_hash_bound_and_outside_immutable_bundle(self):
        session = self.session()
        packet, digest = session.publish(session.ready(), "scan-1")
        self.assertRegex(packet, r"^source-packet-[0-9a-f]{32}\.json$")
        raw = (self.work / packet).read_bytes()
        self.assertEqual(digest, "sha256:" + sha256(raw).hexdigest())
        self.assertEqual(json.loads(raw)["job_id"], "scan-1")
        self.assertEqual(json.loads(raw)["stage_id"], worker.STAGE_ID)
        self.assertEqual(list(self.root.iterdir()), [self.root / "release-manifest.json"])
        self.assertFalse(list(self.work.glob("*.partial")))

    def test_packet_publish_failure_cleans_only_its_partial_and_preserves_old(self):
        session = self.session()
        old, _ = session.publish(session.ready(), "scan-1")
        before = (self.work / old).read_bytes()
        with patch.object(worker.os, "replace", side_effect=OSError("injected rename failure")):
            with self.assertRaises(OSError):
                session.publish(session.ready(), "scan-1")
        self.assertEqual((self.work / old).read_bytes(), before)
        self.assertEqual([path.name for path in self.work.iterdir()], [old])

    def test_packet_collision_and_budget_do_not_overwrite(self):
        session = self.session()
        nonce = SimpleNamespace(hex="1" * 32)
        with patch.object(worker.uuid, "uuid4", return_value=nonce):
            packet, _ = session.publish(session.ready(), "scan-1")
            before = (self.work / packet).read_bytes()
            with self.assertRaises(SourceError):
                session.publish({"different": True}, "scan-1")
        self.assertEqual((self.work / packet).read_bytes(), before)
        with patch.object(worker, "MAX_PACKET", 8), self.assertRaises(SourceError):
            session.publish(session.ready(), "scan-1")
        self.assertFalse(list(self.work.glob("*.partial")))

    def test_wire_stream_rejects_wrong_scan_stage_sequence_and_unknown_args(self):
        command = Envelope.from_dict({"schema_version": 1, "message_type": "command", "message_id": "request",
            "job_id": "scan-1", "stage_id": worker.STAGE_ID, "sequence": 1,
            "payload": {"command": "source_prepare", "args": self.args}})
        self.assertEqual(worker._request(command.to_line(), StreamValidator(), "scan-1"), command)
        with self.assertRaises(SourceError):
            worker._request(command.to_line(), StreamValidator(), "scan-other")
        validator = StreamValidator()
        worker._request(command.to_line(), validator, "scan-1")
        with self.assertRaises(ValueError):
            worker._request(command.to_line(), validator, "scan-1")
        with self.assertRaises(SourceError):
            self.session(cookies="private")

    def test_emitter_uses_v1_checkpoints_and_no_messages_after_shutdown(self):
        stream = io.BytesIO()
        emitter = worker._Emitter("scan-1", stream)
        emitter.checkpoint(("source-packet-" + "1" * 32 + ".json", "sha256:" + "a" * 64))
        emitter.send(MessageType.SHUTDOWN, {"status": "completed"})
        emitter.fail("late-error")
        messages = [Envelope.from_line(line) for line in stream.getvalue().splitlines()]
        validator = StreamValidator()
        for message in messages:
            validator.accept(message)
        self.assertEqual([message.sequence for message in messages], [1, 2])
        self.assertTrue(validator.terminal)
        self.assertEqual(messages[0].payload["artifact_hash"], "sha256:" + "a" * 64)

    def test_direct_isolated_entrypoint_imports_without_checkout_environment(self):
        command = Envelope.from_dict({"schema_version": 1, "message_type": "command", "message_id": "request",
            "job_id": "scan-1", "stage_id": worker.STAGE_ID, "sequence": 1,
            "payload": {"command": "wrong-command", "args": {}}})
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(Path(worker.__file__).resolve())],
            input=command.to_line(), capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"")


class SourceWorkerProcessControlTests(unittest.TestCase):
    """Real process/v1 control with substituted admission and a blocked page.

    These prove interruption/EOF framing, not SDK, installed runtime origin or
    durable recovery. Installed acceptance must rerun with the real producer.
    """

    def setUp(self):
        script = '''import sys,time
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from engine.dubflow.download.enumeration import worker
class BlockedSession:
    def __init__(self,args): pass
    def ready(self): return {"kind":"source-ready"}
    def publish(self,document,job): return ("source-packet-"+"1"*32+".json","sha256:"+"a"*64)
    def page(self,args):
        sys.stderr.buffer.write(b"PAGE_STARTED\\n");sys.stderr.buffer.flush()
        time.sleep(30)
        return {"page":{"completed":True}}
with patch.object(worker,"SourceSession",BlockedSession):
    raise SystemExit(worker.main())
'''
        root = str(Path(__file__).resolve().parents[2])
        self.process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", script, root],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.addCleanup(self.close_process)
        self.output, self.diagnostic = queue.Queue(), queue.Queue()
        def read_lines(stream, sink):
            for line in iter(stream.readline, b""):
                sink.put(line)
            sink.put(None)
        self.readers = [threading.Thread(target=read_lines, args=(self.process.stdout, self.output), daemon=True),
                        threading.Thread(target=read_lines, args=(self.process.stderr, self.diagnostic), daemon=True)]
        for reader in self.readers:
            reader.start()
        self.validator = StreamValidator()
        self.send(1, MessageType.COMMAND, {"command": "source_prepare", "args": {}})
        self.assertEqual(self.receive().message_type, MessageType.CHECKPOINT)
        self.send(2, MessageType.COMMAND, {"command": "source_page", "args": {}})
        self.assertEqual(self.diagnostic.get(timeout=5), b"PAGE_STARTED\n")

    def close_process(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        if not self.process.stdin.closed:
            self.process.stdin.close()
        for reader in self.readers:
            reader.join(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()

    def send(self, sequence, kind, payload, job_id="scan-1"):
        message = Envelope.from_dict({"schema_version": 1, "message_type": kind.value,
            "message_id": "control-" + str(sequence), "job_id": job_id,
            "stage_id": worker.STAGE_ID, "sequence": sequence, "payload": payload})
        self.process.stdin.write(message.to_line())
        self.process.stdin.flush()

    def receive(self):
        line = self.output.get(timeout=5)
        self.assertIsNotNone(line, "unexpected worker EOF")
        message = Envelope.from_line(line)
        self.validator.accept(message)
        return message

    def test_cancel_interrupts_an_active_page_without_inventing_committed_packet(self):
        self.send(3, MessageType.CANCEL, {"reason": "user stopped"})
        checkpoint = self.receive()
        self.assertEqual(checkpoint.message_type, MessageType.CHECKPOINT)
        self.assertEqual(checkpoint.payload["checkpoint_id"], "source-last-supervisor-commit")
        self.assertNotIn("artifact_hash", checkpoint.payload)
        terminal = self.receive()
        self.assertEqual(terminal.payload["status"], "cancelled")
        self.assertEqual(self.process.wait(timeout=5), 0)

    def test_parent_eof_interrupts_active_page_as_failure(self):
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=5), 2)
        self.assertIsNone(self.output.get(timeout=5))

    def test_wrong_scan_control_interrupts_without_logging_private_payload(self):
        self.send(3, MessageType.CANCEL, {"reason": "Cookie: private-control"}, job_id="other-scan")
        failure, terminal = self.receive(), self.receive()
        self.assertEqual(failure.payload["code"], "SOURCE_WORKER_REQUEST_INVALID")
        self.assertNotIn("private-control", json.dumps(failure.to_dict()))
        self.assertEqual(terminal.payload["status"], "failed")
        self.assertEqual(self.process.wait(timeout=5), 2)


if __name__ == "__main__":
    unittest.main()
