from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from packaging.release.manifest import ReleaseArtifact, ReleaseManifest, dump_manifest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release/source_runtime_smoke.py"
spec = importlib.util.spec_from_file_location("source_runtime_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def manifest():
    return ReleaseManifest("0.1.0-source-smoke", "a" * 40, "3.12.10", "v1", "windows", "x86_64",
                           "2026-10-08T00:00:00Z", "candidate", False, "none", "none", "",
                           (ReleaseArtifact("recorded", "runtime/python.exe", 1, "0" * 64),))


class RuntimeSmokeTests(unittest.TestCase):
    def test_wrong_source_or_failed_tree_verification_cannot_execute_sdk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dump_manifest(manifest(), root / "release-manifest.json")
            with patch.object(smoke, "source_runtime_health") as health, patch.object(smoke, "verify_bundle") as verify:
                with self.assertRaises(ValueError):
                    smoke.qualify(root, "b" * 40)
                verify.assert_not_called()
                health.assert_not_called()
                verify.side_effect = RuntimeError("recorded integrity failure")
                with self.assertRaises(RuntimeError):
                    smoke.qualify(root, "a" * 40)
                health.assert_not_called()

    def test_success_retains_exact_identity_and_no_provider_release_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dump_manifest(manifest(), root / "release-manifest.json")
            with patch.object(smoke, "verify_bundle"), patch.object(smoke, "source_runtime_health", return_value={"recorded": True}) as health:
                result = smoke.qualify(root, "a" * 40)
            health.assert_called_once()
            self.assertEqual(result["source_sha"], "a" * 40)
            self.assertEqual(result["scope"], "source-sdk-runtime-health")
            self.assertFalse(result["production_qualified"])
            self.assertEqual(result["live_source_acquisition"], "not_run")


if __name__ == "__main__":
    unittest.main()
