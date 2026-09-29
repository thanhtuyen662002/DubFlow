from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packaging.release.bootstrap import BootstrapInstallError, install_bundle
from packaging.release.builder import BuildError, build_bundle
from packaging.release.manifest import ManifestError, ReleaseArtifact, ReleaseManifest, load_manifest


SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
SOURCE_DATE_EPOCH = 1_700_000_000


def _runtime(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "python.exe").write_bytes(b"portable-python-fixture")
    (root / "python312.dll").write_bytes(b"portable-python-dll")
    (root / "Lib").mkdir()
    (root / "Lib" / "site.py").write_text("# fixture\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")


class ReleaseBundleTests(unittest.TestCase):
    def test_build_is_deterministic_and_manifest_hashes_every_payload_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            output_a = root / "a"
            output_b = root / "b"
            result_a = build_bundle(
                source_root=".",
                output_dir=output_a,
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            result_b = build_bundle(
                source_root=".",
                output_dir=output_b,
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            self.assertEqual(result_a.bundle_path.read_bytes(), result_b.bundle_path.read_bytes())
            self.assertEqual(result_a.checksum_path.read_text(encoding="utf-8"), result_b.checksum_path.read_text(encoding="utf-8"))
            manifest = load_manifest(result_a.staging_dir / "release-manifest.json")
            self.assertFalse(manifest.signature_required)
            self.assertEqual(manifest.release_channel, "candidate")
            for artifact in manifest.artifacts:
                path = result_a.staging_dir / artifact.path
                self.assertEqual(path.stat().st_size, artifact.size_bytes)
                self.assertEqual(sha256(path.read_bytes()).hexdigest(), artifact.sha256)
            self.assertFalse((result_a.staging_dir / "runtime" / "__pycache__").exists())

    def test_install_verifies_then_activates_versioned_pointer_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            install_root = root / "DubFlow App Data"
            report = install_bundle(result.staging_dir, install_root)
            self.assertTrue(report["ready"])
            self.assertEqual(report["version"], "0.1.0-rc1")
            self.assertTrue((install_root / "current.json").is_file())
            self.assertTrue((install_root / "DubFlow.cmd").is_file())
            second = install_bundle(result.staging_dir, install_root)
            self.assertEqual(second["manifest_sha256"], report["manifest_sha256"])
            self.assertEqual(json.loads((install_root / "current.json").read_text(encoding="utf-8"))["current_version"], "0.1.0-rc1")
            (install_root / "versions" / "0.1.0-rc1" / "runtime" / "python.exe").write_bytes(b"tampered")
            with self.assertRaisesRegex(BootstrapInstallError, "INSTALLED_VERSION_INVALID"):
                install_bundle(result.staging_dir, install_root)

    def test_corruption_is_rejected_before_destination_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            target = result.staging_dir / "setup.cmd"
            target.write_text(target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            install_root = root / "install"
            with self.assertRaisesRegex(BootstrapInstallError, "SIZE_MISMATCH|HASH_MISMATCH"):
                install_bundle(result.staging_dir, install_root)
            self.assertFalse((install_root / "current.json").exists())

    def test_manifest_wire_format_preserves_large_u64_as_decimal_string(self) -> None:
        artifact = ReleaseArtifact("large", "payload/large.bin", 2**53 + 1, "a" * 64)
        value = artifact.to_dict()
        self.assertEqual(value["size_bytes"], str(2**53 + 1))
        manifest = ReleaseManifest(
            "0.1.0-rc1",
            SOURCE_SHA,
            "3.12.10",
            "v1",
            "windows",
            "x86_64",
            "2023-11-14T22:13:20Z",
            "candidate",
            False,
            "none",
            "unsigned-candidate",
            "",
            (artifact,),
        )
        decoded = ReleaseManifest.from_mapping(manifest.to_dict())
        self.assertEqual(decoded.artifacts[0].size_bytes, 2**53 + 1)

    def test_unsafe_paths_and_stable_unsigned_release_fail_closed(self) -> None:
        with self.assertRaises(ManifestError):
            ReleaseArtifact("escape", "../outside", 1, "a" * 64)
        with self.assertRaises(BuildError):
            build_bundle(
                source_root=".",
                output_dir=Path("tests") / "release" / "_out",
                version="0.1.0",
                source_sha=SOURCE_SHA,
                runtime_root=Path("tests") / "release" / "missing-runtime",
                release_channel="stable",
            )

    def test_setup_bootstrap_is_user_owned_and_embeds_the_release_payload(self) -> None:
        source = Path("packaging/release/windows_setup.rs").read_text(encoding="utf-8")
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('include_bytes!(env!("DUBFLOW_PAYLOAD"))', source)
        self.assertIn("Expand-Archive", source)
        self.assertIn("drop(output)", source)
        self.assertIn('format!("call \\\"{}\\\"", setup.display())', source)
        self.assertNotIn("requireAdministrator", source)
        self.assertNotIn("7z.sfx", workflow)
        self.assertIn("target-feature=+crt-static", workflow)


if __name__ == "__main__":
    unittest.main()
