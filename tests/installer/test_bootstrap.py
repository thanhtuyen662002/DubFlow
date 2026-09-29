from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from models.bootstrap import BootstrapArtifact
from packaging.runtime.bootstrap import BootstrapController, BootstrapError, HardwareProfile
from packaging.windows.installer import WindowsInstaller


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


if __name__ == "__main__":
    unittest.main()
