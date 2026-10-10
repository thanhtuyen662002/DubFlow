"""Inventory rejection tests; fixtures never qualify actual native startup."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packaging.release.bootstrap import BootstrapInstallError, verify_bundle
from packaging.release.builder import build_bundle
from scripts.release import desktop_runtime_smoke as smoke


class DesktopRuntimeSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        runtime = self.work / "runtime"
        runtime.mkdir()
        (runtime / "python.exe").write_bytes(b"fixture-owned-interpreter")
        (runtime / "python312.dll").write_bytes(b"fixture-dll")
        result = build_bundle(source_root=".", output_dir=self.work / "bundle",
            version="0.1.0-rc.desktop", source_sha="a" * 40, runtime_root=runtime,
            source_date_epoch=1_700_000_000)
        self.root = result.staging_dir
        self.manifest = result.manifest
        self.data = self.work / "LocalAppData/DubFlow"
        self.cache = self.data / "control/webview2" / self.manifest.version.encode("ascii").hex()
        self.browser = self.cache / "EBWebView"
        self.browser.mkdir(parents=True)

    def qualify(self):
        return smoke.qualify_profile(self.root, self.data, self.manifest,
            verify_bundle, cache_timeout=0)

    def initialize_fixture(self):
        (self.browser / "cache-fixture").write_bytes(b"fixture initialization only")

    def test_external_browser_data_preserves_full_runtime_inventory(self):
        self.initialize_fixture()
        result = self.qualify()
        self.assertEqual(result["post_launch_inventory"], "passed")
        self.assertEqual(result["source_sha"], self.manifest.source_sha)
        self.assertEqual(result["webview_file_count"], 1)
        self.assertFalse(Path(result["webview_data_root"]).is_relative_to(self.root))
        self.assertEqual(result["gui_usability"], "NOT_RUN")
        self.assertFalse(result["production_qualified"])

    def test_original_cache_failure_is_rejected_even_with_external_cache(self):
        self.initialize_fixture()
        misplaced = self.root / "app/bin/DubFlow.exe.WebView2/EBWebView/Crashpad/metadata"
        misplaced.parent.mkdir(parents=True)
        misplaced.write_bytes(b"actual regression shape, fixture data")
        with self.assertRaisesRegex(BootstrapInstallError, "UNMANIFESTED_ARTIFACT"):
            self.qualify()

    def test_tampered_manifest_artifact_is_rejected(self):
        self.initialize_fixture()
        interpreter = self.root / "runtime/python.exe"
        interpreter.write_bytes(b"x" * interpreter.stat().st_size)
        with self.assertRaisesRegex(BootstrapInstallError, "HASH_MISMATCH"):
            self.qualify()

    def test_created_directory_is_not_webview_initialization(self):
        with self.assertRaisesRegex(RuntimeError, "actual WebView2 user data was not created"):
            self.qualify()

    def test_mutation_during_browser_initialization_fails_second_inventory(self):
        self.initialize_fixture()
        checks = []
        def verify(root, manifest):
            verify_bundle(root, manifest)
            checks.append(root)
            if len(checks) == 1:
                (root / "late-cache").write_bytes(b"late unmanifested write")
        with self.assertRaisesRegex(BootstrapInstallError, "UNMANIFESTED_ARTIFACT"):
            smoke.qualify_profile(self.root, self.data, self.manifest, verify, cache_timeout=0)
        self.assertEqual(len(checks), 1)

    def test_profile_inside_runtime_and_foreign_interpreter_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "inside the immutable runtime"):
            smoke.qualify_profile(self.root, self.root, self.manifest, verify_bundle, cache_timeout=0)
        with self.assertRaisesRegex(RuntimeError, "installed owned interpreter"):
            smoke.main(["--root", str(self.root), "--data-root", str(self.data),
                "--expected-source-sha", self.manifest.source_sha, "--report", str(self.work / "report.json")])
        self.assertFalse((self.work / "report.json").exists())

    def test_isolated_helper_edit_selects_actual_release_regressions(self):
        import json
        import sys
        registry = json.loads(Path("scripts/ci/component_registry.json").read_text(encoding="utf-8"))
        sys.path.insert(0, str(Path("scripts/ci").resolve()))
        try:
            from run_integration import component_affected
        finally:
            sys.path.pop(0)
        selected = [component for component in registry["components"]
            if component_affected(component, {"scripts/release/desktop_runtime_smoke.py"})]
        self.assertIn(["python", "-m", "unittest", "discover", "-s", "tests/release", "-p", "test_*.py"],
            [command for component in selected for command in component["commands"]])


if __name__ == "__main__":
    unittest.main()
