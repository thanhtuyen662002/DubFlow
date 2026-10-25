from __future__ import annotations

import base64
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from urllib.error import URLError

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from engine.dubflow.update import UpdateError
from engine.dubflow.update import client
from engine.dubflow.update.sign import sign_manifest
from engine.dubflow.update.trust import canonical_payload, setup_name, setup_url, verify_manifest
from engine.dubflow.update.versions import compare


ROOT = Path(__file__).resolve().parents[2]
SETUP_BYTES = b"offline-setup-fixture"


def payload(version: str = "0.2.0", channel: str = "stable") -> dict:
    return {
        "schema_version": 1,
        "version": version,
        "channel": channel,
        "published_at_utc": "2026-10-25T00:00:00Z",
        "notes": "Cập nhật an toàn",
        "setup": {
            "name": setup_name(version), "url": setup_url(version),
            "sha256": sha256(SETUP_BYTES).hexdigest(), "size_bytes": len(SETUP_BYTES),
        },
    }


class KeyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.private_b64 = base64.b64encode(self.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode("ascii")
        self.public_b64 = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")

    def sign_unchecked(self, value: dict) -> dict:
        document = deepcopy(value)
        document["signature"] = {
            "algorithm": "ed25519", "key_id": "dubflow-update-v1",
            "value": base64.b64encode(self.key.sign(canonical_payload(value))).decode("ascii"),
        }
        return document


class TrustTests(KeyTestCase):
    def test_signature_roundtrip_and_canonical_non_ascii_json(self) -> None:
        value = payload()
        document = sign_manifest(value, self.private_b64)
        self.assertEqual(verify_manifest(document, self.public_b64), value)
        self.assertNotIn("signature", value)
        self.assertIn("Cập nhật".encode("utf-8"), canonical_payload(value))
        reordered = dict(reversed(list(document.items())))
        self.assertEqual(verify_manifest(reordered, self.public_b64), value)
        value["setup"]["size_bytes"] += 1
        self.assertEqual(document["setup"]["size_bytes"], len(SETUP_BYTES))

    def test_tampering_every_payload_field_fails(self) -> None:
        original = sign_manifest(payload(), self.private_b64)
        changes = [
            ("schema_version", 2), ("version", "0.3.0"), ("channel", "candidate"),
            ("published_at_utc", "2026-10-26T00:00:00Z"), ("notes", "tampered"),
        ]
        for field, value in changes:
            document = deepcopy(original)
            document[field] = value
            with self.subTest(field=field), self.assertRaises(UpdateError):
                verify_manifest(document, self.public_b64)
        for field, value in (("name", "evil.exe"), ("url", "https://example.com/evil.exe"), ("sha256", "f" * 64), ("size_bytes", 1)):
            document = deepcopy(original)
            document["setup"][field] = value
            with self.subTest(field=field), self.assertRaises(UpdateError):
                verify_manifest(document, self.public_b64)
        document = deepcopy(original)
        document["notes"] = "valid schema, invalid signature"
        with self.assertRaises(UpdateError) as caught:
            verify_manifest(document, self.public_b64)
        self.assertEqual(caught.exception.code, "SIGNATURE_INVALID")

    def test_untrusted_missing_and_malformed_signatures_fail_closed(self) -> None:
        for signature in (None, {}, {"algorithm": "rsa", "key_id": "dubflow-update-v1", "value": "a"},
                          {"algorithm": "ed25519", "key_id": "unknown", "value": "a"},
                          {"algorithm": "ed25519", "key_id": "dubflow-update-v1", "value": "!!!"},
                          {"algorithm": "ed25519", "key_id": "dubflow-update-v1", "value": base64.b64encode(b"x" * 64).decode()}):
            document = payload()
            if signature is not None:
                document["signature"] = signature
            with self.subTest(signature=signature), self.assertRaises(UpdateError) as caught:
                verify_manifest(document, self.public_b64)
            self.assertEqual(caught.exception.code, "SIGNATURE_INVALID")
        with self.assertRaises(UpdateError):
            verify_manifest(sign_manifest(payload(), self.private_b64))

    def test_strict_schema_rejects_signed_invalid_values(self) -> None:
        changes = [
            ("schema_version", True), ("schema_version", 2), ("version", "../escape"), ("version", "a" * 65),
            ("channel", "nightly"), ("channel", []), ("published_at_utc", "yesterday"),
            ("published_at_utc", "2026-10-25"), ("published_at_utc", "2026-10-25T00:00:00"),
            ("published_at_utc", "2026-10-25T00:00:00+07:00"), ("published_at_utc", 123),
            ("notes", "x" * 4001), ("notes", None), ("setup", None), ("extra", "unsupported"),
        ]
        for field, value in changes:
            current = payload()
            current[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(UpdateError) as caught:
                verify_manifest(self.sign_unchecked(current), self.public_b64)
            self.assertEqual(caught.exception.code, "MANIFEST_INVALID")
        for field in payload():
            current = payload()
            del current[field]
            with self.subTest(missing=field), self.assertRaises(UpdateError):
                verify_manifest(self.sign_unchecked(current), self.public_b64)
        for field, value in (("name", "../evil.exe"), ("url", setup_url("0.2.0").replace("https:", "http:")),
                             ("url", setup_url("0.2.0") + "?redirect=evil"), ("sha256", "A" * 64),
                             ("sha256", "0" * 63), ("size_bytes", True), ("size_bytes", 0),
                             ("size_bytes", 2_000_000_001), ("size_bytes", 1.5), ("extra", 1)):
            current = payload()
            current["setup"][field] = value
            with self.subTest(setup_field=field, value=value), self.assertRaises(UpdateError) as caught:
                verify_manifest(self.sign_unchecked(current), self.public_b64)
            self.assertEqual(caught.exception.code, "MANIFEST_INVALID")
        for value in ([], None, "string"):
            with self.assertRaises(UpdateError):
                verify_manifest(value, self.public_b64)


class VersionTests(unittest.TestCase):
    def test_numeric_release_and_prerelease_ordering(self) -> None:
        ordered = ["0.1.9", "0.2.0-alpha10", "0.2.0-alpha2", "0.2.0-beta", "0.2.0-rc6", "0.2.0-rc10", "0.2.0", "1.0.0", "1.0.1", "1.0.10"]
        for index, left in enumerate(ordered):
            self.assertEqual(compare(left, left), 0)
            for right in ordered[index + 1:]:
                with self.subTest(left=left, right=right):
                    self.assertEqual(compare(left, right), -1)
                    self.assertEqual(compare(right, left), 1)
        self.assertEqual(compare("1.0", "1.0.0"), 0)


class ClientTests(KeyTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.documents = {}
        self.calls = []
        self.verifier = mock.patch.object(client, "verify_manifest", side_effect=lambda value: verify_manifest(value, self.public_b64))
        self.verifier.start()
        self.addCleanup(self.verifier.stop)

    def release(self, version: str, *, prerelease: bool = False, channel: str = "stable") -> dict:
        url = f"https://example.test/v{version}/update-manifest.json"
        self.documents[url] = json.dumps(sign_manifest(payload(version, channel), self.private_b64)).encode("utf-8")
        return {"tag_name": "v" + version, "prerelease": prerelease, "assets": [{"name": "update-manifest.json", "browser_download_url": url}]}

    def fetch(self, url: str, *, max_bytes: int) -> bytes:
        self.calls.append((url, max_bytes))
        return self.documents[url]

    def releases(self, releases: list) -> None:
        self.documents[client.RELEASES_URL] = json.dumps(releases).encode("utf-8")

    def test_stable_ignores_prereleases_candidate_sees_both(self) -> None:
        self.releases([self.release("0.3.0-rc10", prerelease=True, channel="candidate"), self.release("0.2.0")])
        self.assertEqual(client.check("0.1.0", "stable", fetch=self.fetch)["version"], "0.2.0")
        self.assertEqual(client.check("0.1.0", "candidate", fetch=self.fetch)["version"], "0.3.0-rc10")

    def test_highest_selected_independent_of_api_order(self) -> None:
        self.releases([self.release("0.2.0-rc6", prerelease=True, channel="candidate"), self.release("0.1.5"), self.release("0.2.0-rc10", prerelease=True, channel="candidate")])
        result = client.check("0.1.0", "candidate", fetch=self.fetch)
        self.assertEqual(result["version"], "0.2.0-rc10")
        self.assertEqual(result["notes"], "Cập nhật an toàn")
        self.assertEqual(result["setup"], payload("0.2.0-rc10")["setup"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][1], 64 * 1024)

    def test_no_update_when_current_is_newest_or_no_manifest_asset(self) -> None:
        self.releases([self.release("0.2.0"), {"tag_name": "v9.0.0", "prerelease": False, "assets": []}])
        self.assertEqual(client.check("0.2.0", "stable", fetch=self.fetch), {"available": False})
        self.assertEqual(client.check("1.0.0", "candidate", fetch=self.fetch), {"available": False})
        self.assertEqual(len(self.calls), 2)

    def test_invalid_tags_and_drafts_are_ignored(self) -> None:
        self.releases([
            {**self.release("9.0.0"), "draft": True},
            {**self.release("8.0.0"), "tag_name": "8.0.0"},
            {"tag_name": "vnot-a-version", "prerelease": False, "assets": []}, self.release("0.2.0"),
        ])
        self.assertEqual(client.check("0.1.0", "stable", fetch=self.fetch)["version"], "0.2.0")

    def test_tag_manifest_mismatch_fails(self) -> None:
        release = self.release("0.2.0")
        url = release["assets"][0]["browser_download_url"]
        self.releases([release])
        self.documents[url] = json.dumps(sign_manifest(payload("0.3.0"), self.private_b64)).encode()
        with self.assertRaises(UpdateError) as caught:
            client.check("0.1.0", "stable", fetch=self.fetch)
        self.assertEqual(caught.exception.code, "MANIFEST_INVALID")

    def test_corrupt_highest_manifest_fails_without_downgrade(self) -> None:
        highest = self.release("0.3.0")
        self.releases([self.release("0.2.0"), highest])
        url = highest["assets"][0]["browser_download_url"]
        document = json.loads(self.documents[url])
        document["notes"] = "tampered"
        self.documents[url] = json.dumps(document).encode()
        with self.assertRaises(UpdateError) as caught:
            client.check("0.1.0", "stable", fetch=self.fetch)
        self.assertEqual(caught.exception.code, "SIGNATURE_INVALID")

    def test_manifest_size_and_json_validation(self) -> None:
        release = self.release("0.2.0")
        self.releases([release])
        url = release["assets"][0]["browser_download_url"]
        for value in (b" " * (64 * 1024 + 1), b"not-json", b"\xff"):
            self.documents[url] = value
            with self.assertRaises(UpdateError) as caught:
                client.check("0.1.0", "stable", fetch=self.fetch)
            self.assertEqual(caught.exception.code, "MANIFEST_INVALID")

    def test_network_failures_are_structured(self) -> None:
        for error in (URLError("offline"), TimeoutError("timeout")):
            with self.subTest(error=error), self.assertRaises(UpdateError) as caught:
                client.check("0.1.0", "stable", fetch=mock.Mock(side_effect=error))
            self.assertEqual(caught.exception.code, "NETWORK")

    def test_default_transport_headers_timeout_and_bounded_read(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"[]"
        with mock.patch.object(client, "urlopen", return_value=response) as opener:
            self.assertEqual(client.urllib_fetch(client.RELEASES_URL, max_bytes=10), b"[]")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, client.RELEASES_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(request.get_header("User-agent"), "DubFlow-updater")
        self.assertEqual(opener.call_args.kwargs["timeout"], 15)
        response.read.assert_called_once_with(11)


class DownloadTests(unittest.TestCase):
    def test_verified_download_atomic_publish_and_progress(self) -> None:
        with TemporaryDirectory() as directory:
            progress = []
            path = client.download(payload()["setup"], Path(directory), fetch_stream=lambda url: [SETUP_BYTES[:4], SETUP_BYTES[4:]], progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(path.read_bytes(), SETUP_BYTES)
            self.assertEqual(path.name, setup_name("0.2.0"))
            self.assertFalse(path.with_name(path.name + ".partial").exists())
            self.assertEqual(progress, [(4, len(SETUP_BYTES)), (len(SETUP_BYTES), len(SETUP_BYTES))])

    def test_hash_short_and_oversize_mismatches_cleanup_preserve_existing(self) -> None:
        for data in (b"x" * len(SETUP_BYTES), SETUP_BYTES[:-1], SETUP_BYTES + b"extra"):
            with self.subTest(data=data), TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / setup_name("0.2.0")
                target.write_bytes(b"previous-verified-setup")
                with self.assertRaises(UpdateError) as caught:
                    client.download(payload()["setup"], root, fetch_stream=lambda url: [data])
                self.assertEqual(caught.exception.code, "DOWNLOAD_CORRUPT")
                self.assertEqual(target.read_bytes(), b"previous-verified-setup")
                self.assertEqual(list(root.glob("*.partial")), [])

    def test_oversize_aborts_before_consuming_more_chunks_and_closes(self) -> None:
        closed = []

        def stream(url):
            try:
                yield SETUP_BYTES + b"extra"
                self.fail("oversized download must stop immediately")
            finally:
                closed.append(True)

        with TemporaryDirectory() as directory, self.assertRaises(UpdateError):
            client.download(payload()["setup"], Path(directory), fetch_stream=stream)
        self.assertEqual(closed, [True])

    def test_network_interrupt_removes_partial(self) -> None:
        def stream(url):
            yield SETUP_BYTES[:2]
            raise URLError("offline")

        with TemporaryDirectory() as directory:
            with self.assertRaises(UpdateError) as caught:
                client.download(payload()["setup"], Path(directory), fetch_stream=stream)
            self.assertEqual(caught.exception.code, "NETWORK")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_untrusted_setup_paths_rejected_before_io(self) -> None:
        setup = payload()["setup"]
        setup["name"] = "../escape.exe"
        with TemporaryDirectory() as directory:
            fetch = mock.Mock()
            with self.assertRaises(UpdateError):
                client.download(setup, Path(directory) / "not-created", fetch_stream=fetch)
            fetch.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])


class CliTests(KeyTestCase):
    def invoke(self, arguments: list[str], **kwargs) -> dict:
        from engine.dubflow.update import __main__ as cli

        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(cli, "check", **kwargs):
            self.assertEqual(cli.main(arguments), 0)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        return json.loads(output.getvalue())

    def test_check_success_no_update_and_errors_are_one_json_line(self) -> None:
        arguments = ["check", "--current", "0.1.0", "--channel", "stable"]
        self.assertEqual(self.invoke(arguments, return_value={"available": False}), {"ok": True, "available": False})
        failure = self.invoke(arguments, side_effect=UpdateError("NETWORK", "offline"))
        self.assertEqual(failure, {"ok": False, "code": "NETWORK", "message": "offline"})
        result = self.invoke(arguments, return_value={"available": True, **payload()})
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "0.2.0")

    def test_download_rechecks_and_returns_path_version(self) -> None:
        from engine.dubflow.update import __main__ as cli

        with TemporaryDirectory() as directory, mock.patch.object(cli, "download", return_value=Path(directory) / setup_name("0.2.0")) as downloader:
            arguments = ["download", "--current", "0.1.0", "--channel", "stable", "--dest", directory]
            result = self.invoke(arguments, return_value={"available": True, **payload()})
            self.assertEqual(result, {"ok": True, "path": str(Path(directory) / setup_name("0.2.0")), "version": "0.2.0"})
            downloader.assert_called_once_with(payload()["setup"], Path(directory))
            downloader.reset_mock()
            self.assertEqual(self.invoke(arguments, return_value={"available": False}), {"ok": True, "available": False})
            downloader.assert_not_called()

    def test_module_cli_json_and_usage_exit_codes_offline(self) -> None:
        result = subprocess.run([sys.executable, "-B", "-m", "engine.dubflow.update", "check", "--current", "invalid", "--channel", "stable"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"ok", "code", "message"})
        self.assertEqual(document["code"], "MANIFEST_INVALID")
        usage = subprocess.run([sys.executable, "-B", "-m", "engine.dubflow.update", "check"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(usage.returncode, 2)

    def test_sign_cli_uses_environment_key_and_writes_verified_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            setup = root / setup_name("0.2.0")
            setup.write_bytes(SETUP_BYTES)
            notes = root / "notes.txt"
            notes.write_text("Cập nhật an toàn", encoding="utf-8")
            output = root / "update-manifest.json"
            arguments = [sys.executable, "-B", "-m", "engine.dubflow.update.sign", "--key-env", "DUBFLOW_TEST_SIGN_KEY", "--version", "0.2.0", "--channel", "stable", "--setup", str(setup), "--notes-file", str(notes), "--out", str(output)]
            environment = {**os.environ, "DUBFLOW_TEST_SIGN_KEY": self.private_b64}
            result = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertTrue(json.loads(result.stdout)["ok"])
            self.assertNotIn(self.private_b64, result.stdout + result.stderr + output.read_text(encoding="utf-8"))
            verified = verify_manifest(json.loads(output.read_text(encoding="utf-8")), self.public_b64)
            self.assertEqual(verified["setup"], payload()["setup"])
            self.assertEqual(verified["notes"], "Cập nhật an toàn")
            environment["DUBFLOW_TEST_SIGN_KEY"] = "private-invalid-secret"
            bad = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertFalse(json.loads(bad.stdout)["ok"])
            self.assertNotIn(environment["DUBFLOW_TEST_SIGN_KEY"], bad.stdout + bad.stderr)

    def test_app_root_goes_after_site_packages_for_cli_imports(self) -> None:
        from engine.dubflow.update import __main__ as cli

        cli._app_root_last()
        self.assertEqual(Path(sys.path[-1]), ROOT)
        script = "import engine.dubflow.update.__main__; import packaging; print(packaging.__file__)"
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(Path(result.stdout.strip()).resolve(), ROOT / "packaging/__init__.py")


if __name__ == "__main__":
    unittest.main()
