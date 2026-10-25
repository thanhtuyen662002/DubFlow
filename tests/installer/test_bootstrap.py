from __future__ import annotations

from hashlib import sha256
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from models.bootstrap import BootstrapArtifact
from packaging.runtime.bootstrap import BootstrapController, BootstrapError, HardwareProfile
from packaging.windows.installer import WindowsInstaller
from packaging.release.bootstrap import BootstrapInstallError, activate_version, install_bundle, list_versions, main
from packaging.release.manifest import ReleaseArtifact, ReleaseManifest, dump_manifest


def artifact(identifier: str, payload: bytes, *, required: bool = True, fallback: str | None = None) -> BootstrapArtifact:
    return BootstrapArtifact(identifier, "1.0.0", "model", len(payload), sha256(payload).hexdigest(), "fixture-signature", required, fallback)


class BootstrapTests(unittest.TestCase):
    def test_resume_hash_signature_atomic_publish_and_non_ascii_path(self) -> None:
        payload = b"runtime-payload"
        item = artifact("runtime.cpu", payload)
        with TemporaryDirectory(prefix="Dub Flow ") as directory:
            root = Path(directory) / "App Data 空间"
            package_dir = root / "packages"
            package_dir.mkdir(parents=True)
            partial = package_dir / "runtime.cpu-1.0.0.pkg.partial"
            partial.write_bytes(payload[:7])
            calls: list[int] = []

            def fetcher(current: BootstrapArtifact, offset: int) -> bytes:
                calls.append(offset)
                return payload[offset : offset + 3]

            installer = WindowsInstaller(root, hardware=HardwareProfile("x86_64", 4, 8 * 1024**3, "cpu"), signature_verifier=lambda current, digest: current.signature == "fixture-signature" and digest == current.sha256)
            report = installer.bootstrap([item], fetcher)
            self.assertTrue(report.ready)
            self.assertEqual(calls[0], 7)
            target = package_dir / "runtime.cpu-1.0.0.pkg"
            self.assertEqual(target.read_bytes(), payload)
            second = installer.bootstrap([item], lambda current, offset: self.fail("valid install must be idempotent"))
            self.assertTrue(second.ready)

    def test_checksum_or_signature_failure_leaves_actionable_state(self) -> None:
        item = artifact("bad", b"expected")
        with TemporaryDirectory() as directory:
            controller = BootstrapController(directory, signature_verifier=lambda current, digest: False)
            report = controller.install([item], lambda current, offset: b"wrong"[offset:])
            self.assertFalse(report.ready)
            self.assertEqual(report.failures[0]["code"], "DOWNLOAD_EMPTY")
            self.assertFalse((Path(directory) / "packages" / "bad-1.0.0.pkg").exists())
        with TemporaryDirectory() as directory:
            controller = BootstrapController(directory, signature_verifier=lambda current, digest: False)
            valid = b"expected"
            report = controller.install([item], lambda current, offset: valid[offset:])
            self.assertFalse(report.ready)
            self.assertEqual(report.failures[0]["code"], "SIGNATURE_INVALID")

    def test_low_disk_and_unsafe_path_are_rejected_before_download(self) -> None:
        item = artifact("runtime", b"123456")
        with TemporaryDirectory() as directory:
            controller = BootstrapController(directory, free_bytes=2)
            with self.assertRaisesRegex(BootstrapError, "LOW_DISK"):
                controller.install([item], lambda current, offset: b"123456")
        with self.assertRaises(ValueError):
            BootstrapArtifact("../escape", "1", "model", 1, "0" * 64, "sig")

    def test_optional_gpu_pack_falls_back_to_verified_cpu_pack(self) -> None:
        cpu_payload = b"cpu"
        gpu_payload = b"gpu"
        cpu = artifact("runtime.cpu", cpu_payload)
        gpu = BootstrapArtifact("runtime.gpu", "1.0.0", "model", len(gpu_payload), sha256(gpu_payload).hexdigest(), "fixture-signature", False, "runtime.cpu", ("cuda_x86",))
        with TemporaryDirectory() as directory:
            installer = WindowsInstaller(directory, hardware=HardwareProfile("x86_64", 2, 4 * 1024**3, "cpu"), signature_verifier=lambda current, digest: True)
            report = installer.bootstrap([gpu, cpu], lambda current, offset: (cpu_payload if current.artifact_id == "runtime.cpu" else gpu_payload)[offset:])
            self.assertTrue(report.ready)
            self.assertEqual(report.fallbacks, ("runtime.gpu->runtime.cpu",))
            self.assertTrue(report.ready)
            self.assertIn("runtime.cpu", report.installed)

    def test_cpu_hardware_profile_is_usable_without_gpu(self) -> None:
        profile = HardwareProfile.detect(accelerator="cpu")
        self.assertGreaterEqual(profile.cpu_count, 1)
        self.assertEqual(profile.accelerator, "cpu")


def release_bundle(root: Path, version: str, *, qualified: bool = False, status: bytes | None = None) -> Path:
    """Small complete bundle, independent of the site-packages tests namespace."""

    source_sha = "a" * 40
    payloads = {
        "runtime/python.exe": b"portable-python",
        "app/packaging/release/launcher.py": b"# launcher fixture\n",
        "release-status.json": status if status is not None else json.dumps({
            "schema_version": 1,
            "version": version,
            "source_sha": source_sha,
            "release_channel": "candidate",
            "production_qualified": qualified,
        }).encode("utf-8"),
    }
    artifacts = []
    for index, (relative, data) in enumerate(payloads.items()):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        artifacts.append(ReleaseArtifact(f"file-{index}", relative, len(data), sha256(data).hexdigest()))
    dump_manifest(ReleaseManifest(
        version, source_sha, "3.12.10", "v1", "windows", "x86_64", "2026-10-25T00:00:00Z",
        "candidate", False, "none", "unsigned-candidate", "", tuple(artifacts),
    ), root / "release-manifest.json")
    return root


class VersionActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / "DubFlow"
        install_bundle(release_bundle(self.root / "old", "0.1.0", qualified=True), self.install)
        install_bundle(release_bundle(self.root / "new", "0.2.0-rc10"), self.install)

    def snapshot(self) -> dict[str, bytes]:
        return {name: (self.install / name).read_bytes() for name in ("current.json", "install-state.json", "DubFlow.cmd")}

    def test_activate_switches_pointer_launcher_and_qualification_state(self) -> None:
        report = activate_version(self.install, "0.1.0")
        self.assertTrue(report["ready"])
        self.assertTrue(report["production_qualified"])
        pointer = json.loads((self.install / "current.json").read_text(encoding="utf-8"))
        state = json.loads((self.install / "install-state.json").read_text(encoding="utf-8"))
        self.assertEqual(pointer["current_version"], "0.1.0")
        self.assertEqual(pointer["version_path"], "versions/0.1.0")
        self.assertEqual(state["manifest_sha256"], pointer["manifest_sha256"])
        self.assertEqual(state["current_version"], "0.1.0")
        self.assertEqual(state["release_channel"], "candidate")
        self.assertTrue(state["production_qualified"])
        self.assertIn(r"versions\0.1.0\runtime\python.exe", (self.install / "DubFlow.cmd").read_text(encoding="utf-8"))
        report = activate_version(self.install, "0.2.0-rc10")
        self.assertFalse(report["production_qualified"])
        self.assertTrue((self.install / "versions/0.1.0").is_dir())

    def test_unknown_and_unsafe_versions_do_not_change_active_install(self) -> None:
        before = self.snapshot()
        for version in ("0.9.0", "../escape", "", "v" * 65, "bad\\path", "bad\n", "bad&cmd"):
            with self.subTest(version=version), self.assertRaises(BootstrapInstallError):
                activate_version(self.install, version)
            self.assertEqual(self.snapshot(), before)

    def test_corrupt_version_does_not_change_active_install(self) -> None:
        before = self.snapshot()
        (self.install / "versions/0.1.0/runtime/python.exe").write_bytes(b"tampered-python")
        with self.assertRaisesRegex(BootstrapInstallError, "HASH_MISMATCH"):
            activate_version(self.install, "0.1.0")
        self.assertEqual(self.snapshot(), before)

    def test_manifest_missing_or_wrong_version_does_not_change_active_install(self) -> None:
        before = self.snapshot()
        path = self.install / "versions/0.1.0/release-manifest.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["version"] = "0.3.0"
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(BootstrapInstallError, "VERSION_CONFLICT"):
            activate_version(self.install, "0.1.0")
        path.unlink()
        with self.assertRaisesRegex(BootstrapInstallError, "MANIFEST_INVALID"):
            activate_version(self.install, "0.1.0")
        self.assertEqual(self.snapshot(), before)

    def test_symlinked_version_is_rejected(self) -> None:
        before = self.snapshot()
        link = self.install / "versions/0.3.0"
        try:
            link.symlink_to(self.install / "versions/0.1.0", target_is_directory=True)
        except OSError:
            self.skipTest("creating symlinks requires Windows Developer Mode or privileges")
        with self.assertRaisesRegex(BootstrapInstallError, "SYMLINK_REJECTED"):
            activate_version(self.install, "0.3.0")
        self.assertEqual(self.snapshot(), before)

    def test_junction_in_version_path_is_rejected_before_read(self) -> None:
        before = self.snapshot()
        version_path = (self.install / "versions/0.1.0").resolve()
        # Exercise the junction branch without Windows link privileges.
        with mock.patch.object(Path, "is_junction", lambda path: path.resolve() == version_path, create=True):
            with self.assertRaisesRegex(BootstrapInstallError, "SYMLINK_REJECTED"):
                activate_version(version_path.parents[1], "0.1.0")
        self.assertEqual(self.snapshot(), before)

    def test_linked_artifact_is_rejected_before_pointer_change(self) -> None:
        before = self.snapshot()
        from packaging.release import bootstrap
        original = bootstrap._is_link
        artifact_path = (self.install / "versions/0.1.0/runtime/python.exe").resolve()
        with mock.patch.object(bootstrap, "_is_link", side_effect=lambda path: path == artifact_path or original(path)):
            with self.assertRaisesRegex(BootstrapInstallError, "SYMLINK_REJECTED"):
                activate_version(self.install, "0.1.0")
        self.assertEqual(self.snapshot(), before)

    def test_invalid_qualification_is_checked_before_pointer_change(self) -> None:
        before = self.snapshot()
        release_bundle(self.install / "versions/0.3.0", "0.3.0", status=b"invalid-json")
        with self.assertRaisesRegex(BootstrapInstallError, "STATUS_INVALID"):
            activate_version(self.install, "0.3.0")
        self.assertEqual(self.snapshot(), before)

    def test_list_ignores_staging_and_sorts_by_mtime(self) -> None:
        versions = self.install / "versions"
        release_bundle(versions / ".0.3.0.staging", "0.3.0")
        os.utime(versions / "0.1.0", (1_700_000_000, 1_700_000_000))
        os.utime(versions / "0.2.0-rc10", (1_700_000_010, 1_700_000_010))
        result = list_versions(self.install)
        self.assertEqual(result["current"], "0.2.0-rc10")
        self.assertEqual([item["version"] for item in result["versions"]], ["0.2.0-rc10", "0.1.0"])
        self.assertEqual(result["versions"][1]["installed_at_utc"], "2023-11-14T22:13:20Z")
        self.assertEqual(result["versions"][0]["channel"], "candidate")
        self.assertEqual(list_versions(self.root / "absent"), {"current": None, "versions": []})

    def test_invalid_manifest_encoding_returns_one_json_error_without_mutation(self) -> None:
        before = self.snapshot()
        (self.install / "versions/0.1.0/release-manifest.json").write_bytes(b"\xff")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--activate", "0.1.0", "--install-root", str(self.install)]), 2)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "MANIFEST_INVALID")
        self.assertEqual(self.snapshot(), before)

    def test_cli_activation_and_listing_each_print_one_json_line(self) -> None:
        for arguments in (["--activate", "0.1.0"], ["--list"]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([*arguments, "--install-root", str(self.install)]), 0)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            self.assertIsInstance(json.loads(output.getvalue()), dict)


if __name__ == "__main__":
    unittest.main()
