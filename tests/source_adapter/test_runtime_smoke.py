from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from packaging.release.manifest import ReleaseArtifact, ReleaseManifest, dump_manifest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release/source_runtime_smoke.py"
spec = importlib.util.spec_from_file_location("source_runtime_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


PROFILE = {"filename": "yt_dlp-2026.8.19-py3-none-any.whl", "sha256": "c" * 64}
PROFILE_BYTES = json.dumps(PROFILE).encode()
PROFILE_SHA = hashlib.sha256(PROFILE_BYTES).hexdigest()


def manifest():
    return ReleaseManifest("0.1.0-source-smoke", "a" * 40, "3.12.10", "v1", "windows", "x86_64",
                           "2026-10-08T00:00:00Z", "candidate", False, "none", "none", "",
                           (ReleaseArtifact("recorded", "runtime/python.exe", 1, "0" * 64),
                            ReleaseArtifact("supervisor", "app/bin/dubflow-supervisor.exe", 1, "d" * 64),
                            ReleaseArtifact("helper", smoke.BUNDLE_HELPER, 1, "b" * 64),
                            ReleaseArtifact("descriptor", smoke.BUNDLE_PROFILE, len(PROFILE_BYTES), PROFILE_SHA),
                            ReleaseArtifact("sdk", "runtime/source/" + PROFILE["filename"], 1, "c" * 64)))


def prepare(root):
    dump_manifest(manifest(), root / "release-manifest.json")
    path = root / smoke.BUNDLE_PROFILE
    path.parent.mkdir(parents=True)
    path.write_bytes(PROFILE_BYTES)


def page_report(root):
    return {"status": "passed", "cases": ["recorded"] * 6,
            "generic_playlist_cases": ["recorded"] * 6, "generic_inspection": {"status": "passed"},
            "generic_missing_identity": {"status": "passed"},
            "python": str(root / "runtime/python.exe"), "production_qualified": False}


def generic_factory(root):
    adapter = Mock(provider_id="generic")
    native = adapter._transport._sdk
    native.python = root / "runtime/python.exe"
    native.helper = root / smoke.BUNDLE_HELPER
    native.sdk_archive = root / "runtime/source" / PROFILE["filename"]
    native.pins = {"python": "0" * 64, "helper": "b" * 64, "sdk_archive": "c" * 64}
    adapter._stream_materializer.materializer = adapter._materializer
    return adapter


class RuntimeSmokeTests(unittest.TestCase):
    def setUp(self):
        def session_report(root, manifest_hash, source_sha):
            return {"status": "passed", "scope": "native-windows-protected-session-boundary",
                    "source_sha": source_sha, "manifest_sha256": manifest_hash,
                    "supervisor_sha256": "d" * 64,
                    "synthetic_credentials_only": True, "network_requests": 0,
                    "cases": list(smoke.SESSION_CASES)}
        patcher = patch.object(smoke, "qualify_source_sessions", side_effect=session_report)
        self.sessions = patcher.start()
        self.addCleanup(patcher.stop)

    def test_native_session_failure_or_wrong_binding_cannot_pass_runtime_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare(root)
            for result in ({"status": "failed"}, {"status": "passed", "source_sha": "b" * 40}):
                self.sessions.side_effect = None
                self.sessions.return_value = result
                with self.subTest(result=result), patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", return_value={"sdk_sha256": "c" * 64}), patch.object(smoke, "qualify_sdk_pages", return_value=page_report(root)), patch.object(smoke, "provider_from_verified_bundle", return_value=generic_factory(root)):
                    with self.assertRaisesRegex(ValueError, "native protected-session"):
                        smoke.qualify(root, "a" * 40)

    def test_wrong_source_or_failed_tree_verification_cannot_execute_sdk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dump_manifest(manifest(), root / "release-manifest.json")
            with patch.object(smoke, "source_runtime_health") as health, patch.object(smoke, "verify_bundle") as verify, patch.object(smoke, "qualify_sdk_pages") as pages:
                with self.assertRaises(ValueError):
                    smoke.qualify(root, "b" * 40)
                verify.assert_not_called()
                health.assert_not_called()
                pages.assert_not_called()
                verify.side_effect = RuntimeError("recorded integrity failure")
                with self.assertRaises(RuntimeError):
                    smoke.qualify(root, "a" * 40)
                health.assert_not_called()
                pages.assert_not_called()

    def test_success_retains_exact_identity_and_no_provider_release_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare(root)
            with patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", return_value={"sdk_sha256": "c" * 64}) as health, patch.object(smoke, "qualify_sdk_pages", return_value=page_report(root)) as pages, patch.object(smoke, "provider_from_verified_bundle", return_value=generic_factory(root)):
                result = smoke.qualify(root, "a" * 40)
            health.assert_called_once()
            pages.assert_called_once_with(root / "runtime/source" / PROFILE["filename"],
                root / smoke.BUNDLE_HELPER, root / smoke.BUNDLE_PROFILE,
                helper_sha256="b" * 64, descriptor_sha256=PROFILE_SHA)
            self.assertEqual(result["source_sha"], "a" * 40)
            self.assertEqual(result["scope"], "source-sdk-runtime-health")
            self.assertFalse(result["production_qualified"])
            self.assertEqual(result["live_source_acquisition"], "not_run")
            self.assertEqual(result["durable_intake_enumeration"], "not_run")
            self.assertEqual(result["offline_sdk_pages"], page_report(root))
            self.assertEqual(result["generic_factory"]["status"], "passed")

    def test_failed_runtime_health_cannot_start_sdk_page_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare(root)
            with patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", side_effect=RuntimeError("recorded owned runtime failure")), patch.object(smoke, "qualify_sdk_pages") as pages:
                with self.assertRaises(RuntimeError):
                    smoke.qualify(root, "a" * 40)
                pages.assert_not_called()

    def test_descriptor_changed_after_health_cannot_select_external_sdk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare(root)
            def health(*args, **kwargs):
                (root / smoke.BUNDLE_PROFILE).write_text(json.dumps({"filename": "../../foreign.whl"}))
                return {"sdk_sha256": "c" * 64}
            with patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", side_effect=health), patch.object(smoke, "qualify_sdk_pages") as pages:
                with self.assertRaisesRegex(ValueError, "descriptor differs"):
                    smoke.qualify(root, "a" * 40)
                pages.assert_not_called()

    def test_foreign_interpreter_or_incomplete_pages_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare(root)
            for change in ({"python": str(root.parent / "foreign/python.exe")},
                           {"status": "failed"}, {"cases": ["recorded"] * 5},
                           {"generic_playlist_cases": []}, {"generic_inspection": {"status": "failed"}},
                           {"generic_missing_identity": {}}, {"generic_missing_identity": {"status": "failed"}}):
                with self.subTest(change=change), patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", return_value={"sdk_sha256": "c" * 64}), patch.object(smoke, "qualify_sdk_pages", return_value={**page_report(root), **change}), patch.object(smoke, "provider_from_verified_bundle", return_value=generic_factory(root)):
                    with self.assertRaisesRegex(ValueError, "owned interpreter"):
                        smoke.qualify(root, "a" * 40)

    def test_foreign_generic_factory_producer_cannot_start_recorded_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepare(root)
            adapter = generic_factory(root)
            adapter._transport._sdk.python = root.parent / "foreign/python.exe"
            with patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", return_value={"sdk_sha256": "c" * 64}), patch.object(smoke, "qualify_sdk_pages") as pages, patch.object(smoke, "provider_from_verified_bundle", return_value=adapter):
                with self.assertRaisesRegex(ValueError, "generic factory"):
                    smoke.qualify(root, "a" * 40)
                pages.assert_not_called()


if __name__ == "__main__":
    unittest.main()
