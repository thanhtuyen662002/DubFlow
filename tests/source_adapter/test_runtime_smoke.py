from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
            "python": str(root / "runtime/python.exe"), "production_qualified": False}


class RuntimeSmokeTests(unittest.TestCase):
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
            with patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", return_value={"sdk_sha256": "c" * 64}) as health, patch.object(smoke, "qualify_sdk_pages", return_value=page_report(root)) as pages:
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
                           {"status": "failed"}, {"cases": ["recorded"] * 5}):
                with self.subTest(change=change), patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", return_value={"sdk_sha256": "c" * 64}), patch.object(smoke, "qualify_sdk_pages", return_value={**page_report(root), **change}):
                    with self.assertRaisesRegex(ValueError, "owned interpreter"):
                        smoke.qualify(root, "a" * 40)


if __name__ == "__main__":
    unittest.main()
