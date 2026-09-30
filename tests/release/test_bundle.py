from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from packaging.release.bootstrap import BootstrapInstallError, install_bundle
from packaging.release.builder import BuildError, build_bundle
from packaging.release.manifest import ManifestError, ReleaseArtifact, ReleaseManifest, hash_file, load_manifest


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


def _desktop_binary(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    binary = root / "DubFlow.exe"
    binary.write_bytes(b"MZ-dubflow-host-fixture")
    return binary


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

    def test_generated_cmd_keeps_bundle_root_quote_terminated(self) -> None:
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
            setup = (result.staging_dir / "setup.cmd").read_text(encoding="utf-8")
            self.assertIn('set "BUNDLE_ROOT=%~dp0."', setup)
            self.assertIn('"%BUNDLE_ROOT%\\runtime\\python.exe"', setup)
            self.assertIn('"%BUNDLE_ROOT%\\runtime\\python.exe" -B', setup)
            self.assertIn('"%BUNDLE_ROOT%\\app\\packaging\\release\\bootstrap.py"', setup)
            self.assertIn('--bundle-root "%BUNDLE_ROOT%" --install-root "%LOCALAPPDATA%\\DubFlow"', setup)
            self.assertIn('start "" /b "%LOCALAPPDATA%\\DubFlow\\DubFlow.cmd"', setup)
            self.assertNotIn('--bundle-root "%~dp0%"', setup)

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
        for unsafe_path in ("../outside", "app/file:stream", "app/CON.txt", "app/file. ", "app\\file"):
            with self.assertRaises(ManifestError):
                ReleaseArtifact("escape", unsafe_path, 1, "a" * 64)
        with self.assertRaises(ManifestError):
            ReleaseManifest(
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
                (
                    ReleaseArtifact("one", "app/Foo", 0, "a" * 64),
                    ReleaseArtifact("two", "app/foo", 0, "b" * 64),
                ),
            )
        with self.assertRaises(BuildError):
            build_bundle(
                source_root=".",
                output_dir=Path("tests") / "release" / "_out",
                version="0.1.0",
                source_sha=SOURCE_SHA,
                runtime_root=Path("tests") / "release" / "missing-runtime",
                release_channel="stable",
            )

    def test_stable_signature_metadata_fails_closed_even_when_forged(self) -> None:
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
            manifest_path = result.staging_dir / "release-manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["release_channel"] = "stable"
            payload["security"] = {
                "signature_required": True,
                "algorithm": "ed25519",
                "key_id": "attacker-key",
                "value": "forged-signature",
            }
            manifest_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(BootstrapInstallError, "SIGNATURE_TRUST"):
                install_bundle(result.staging_dir, root / "install")

    def test_unmanifested_file_is_rejected_before_destination_mutation(self) -> None:
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
            (result.staging_dir / "evil.bin").write_bytes(b"not in manifest")
            install_root = root / "install"
            with self.assertRaisesRegex(BootstrapInstallError, "UNMANIFESTED_ARTIFACT"):
                install_bundle(result.staging_dir, install_root)
            self.assertFalse((install_root / "current.json").exists())

    def test_runtime_bytecode_cache_is_transient_and_does_not_block_install(self) -> None:
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
            cache = result.staging_dir / "app" / "packaging" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "generated.cpython-312.pyc").write_bytes(b"transient")
            report = install_bundle(result.staging_dir, root / "install")
            self.assertTrue(report["ready"])

    def test_install_resumes_deterministic_staging_and_removes_progress(self) -> None:
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
            install_root = root / "install"
            staging = install_root / "versions" / ".0.1.0-rc1.staging"
            staging.mkdir(parents=True)
            shutil.copy2(result.staging_dir / "setup.cmd", staging / "setup.cmd")
            manifest_hash = hash_file(result.staging_dir / "release-manifest.json")
            (install_root / "install-progress-0.1.0-rc1.json").parent.mkdir(parents=True, exist_ok=True)
            (install_root / "install-progress-0.1.0-rc1.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "failed",
                        "version": "0.1.0-rc1",
                        "manifest_sha256": manifest_hash,
                        "staging_path": str(staging),
                        "completed": ["setup.cmd"],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            report = install_bundle(result.staging_dir, install_root)
            self.assertTrue(report["ready"])
            self.assertTrue((install_root / "versions" / "0.1.0-rc1").is_dir())
            self.assertFalse(staging.exists())
            self.assertFalse((install_root / "install-progress-0.1.0-rc1.json").exists())

    def test_copy_failure_leaves_actionable_failed_progress_and_staging(self) -> None:
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
            install_root = root / "install"
            with mock.patch("packaging.release.bootstrap.shutil.copy2", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(BootstrapInstallError, "STAGING_COPY_FAILED"):
                    install_bundle(result.staging_dir, install_root)
            progress = json.loads((install_root / "install-progress-0.1.0-rc1.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "failed")
            self.assertIn("STAGING_COPY_FAILED", progress["error"])
            self.assertTrue((install_root / "versions" / ".0.1.0-rc1.staging").is_dir())
            self.assertFalse((install_root / "current.json").exists())

    def test_staged_reverification_rejects_extra_file_and_preserves_failure_state(self) -> None:
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
            install_root = root / "install"
            staging = install_root / "versions" / ".0.1.0-rc1.staging"
            staging.parent.mkdir(parents=True)
            shutil.copytree(result.staging_dir, staging)
            (staging / "evil.bin").write_bytes(b"unmanifested")
            with self.assertRaisesRegex(BootstrapInstallError, "UNMANIFESTED_ARTIFACT"):
                install_bundle(result.staging_dir, install_root)
            self.assertFalse((install_root / "current.json").exists())
            progress = json.loads((install_root / "install-progress-0.1.0-rc1.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "failed")
            self.assertTrue(staging.is_dir())

    def test_launcher_rejects_tampered_pointer_and_candidate_qualification(self) -> None:
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
            install_root = root / "install"
            install_bundle(result.staging_dir, install_root)
            launcher_path = install_root / "versions" / "0.1.0-rc1" / "app" / "packaging" / "release" / "launcher.py"
            spec = importlib.util.spec_from_file_location("installed_dubflow_launcher", launcher_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            launcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(launcher)
            self.assertTrue(launcher.self_check()["ready"])

            current_path = install_root / "current.json"
            current = json.loads(current_path.read_text(encoding="utf-8"))
            current["version_path"] = "versions/other"
            current_path.write_text(json.dumps(current), encoding="utf-8")
            self.assertEqual(launcher.self_check()["code"], "CURRENT_POINTER_MISMATCH")
            current["version_path"] = "versions/0.1.0-rc1"
            current_path.write_text(json.dumps(current), encoding="utf-8")

            status_path = install_root / "versions" / "0.1.0-rc1" / "release-status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["production_qualified"] = True
            status_path.write_text(json.dumps(status), encoding="utf-8")
            self.assertEqual(launcher.self_check()["code"], "STATUS_TAMPERED")

    def test_desktop_host_is_required_for_release_and_verified_before_launch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            host = _desktop_binary(root / "host")
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                desktop_binary=host,
                require_desktop_host=True,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            packaged = result.staging_dir / "app" / "bin" / "DubFlow.exe"
            self.assertEqual(packaged.read_bytes(), host.read_bytes())
            artifact = next(item for item in result.manifest.artifacts if item.path == "app/bin/DubFlow.exe")
            self.assertTrue(artifact.executable)

            install_root = root / "install"
            install_bundle(result.staging_dir, install_root)
            launcher_path = install_root / "versions" / "0.1.0-rc1" / "app" / "packaging" / "release" / "launcher.py"
            spec = importlib.util.spec_from_file_location("installed_dubflow_launcher_host", launcher_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            launcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(launcher)
            process = mock.Mock(pid=4242)
            with mock.patch.object(launcher.subprocess, "Popen", return_value=process) as popen:
                self.assertEqual(launcher.main([]), 0)
            popen.assert_called_once()
            installed_host = install_root / "versions" / "0.1.0-rc1" / "app" / "bin" / "DubFlow.exe"
            self.assertEqual(Path(popen.call_args.args[0][0]).resolve(), installed_host.resolve())

            installed_host.write_bytes(b"tampered")
            with mock.patch.object(launcher.subprocess, "Popen") as popen:
                self.assertEqual(launcher.main([]), 3)
            popen.assert_not_called()

    def test_required_desktop_host_missing_fails_build(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            with self.assertRaisesRegex(BuildError, "desktop host binary is required"):
                build_bundle(
                    source_root=".",
                    output_dir=root / "out",
                    version="0.1.0-rc1",
                    source_sha=SOURCE_SHA,
                    runtime_root=runtime,
                    require_desktop_host=True,
                    source_date_epoch=SOURCE_DATE_EPOCH,
                )

    def test_setup_bootstrap_is_user_owned_and_embeds_the_release_payload(self) -> None:
        source = Path("packaging/release/windows_setup.rs").read_text(encoding="utf-8")
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('include_bytes!(env!("DUBFLOW_PAYLOAD"))', source)
        self.assertIn("Expand-Archive", source)
        self.assertIn("drop(output)", source)
        self.assertIn("system_binary", source)
        self.assertIn('"System32"', source)
        self.assertIn("fs::create_dir(&root)", source)
        self.assertIn('.current_dir(&extracted)', source)
        self.assertIn('"setup.cmd"', source)
        self.assertNotIn("requireAdministrator", source)
        self.assertNotIn("7z.sfx", workflow)
        self.assertIn("target-feature=+crt-static", workflow)
        self.assertIn("actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020", workflow)
        self.assertIn("npm run tauri:build --prefix apps/desktop", workflow)
        self.assertIn("--desktop-binary", workflow)
        self.assertIn("--require-desktop-host", workflow)
        self.assertIn("Smoke install and launch desktop host", workflow)
        self.assertIn("launch_smoke = 'passed'", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("windows-x64.zip.sha256", workflow)
        self.assertIn('--target "$SOURCE_SHA"', workflow)
        self.assertIn('gh release create "$tag" --repo "$GITHUB_REPOSITORY"', workflow)
        self.assertIn('gh release upload "$tag" --repo "$GITHUB_REPOSITORY"', workflow)
        self.assertIn("resolved_sha", workflow)


if __name__ == "__main__":
    unittest.main()
