from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from engine.dubflow.security import (
    ArchiveMember,
    SecurityBoundaryError,
    allocate_filename,
    build_subprocess_plan,
    plan_archive_extract,
    redact_diagnostics,
    validate_relative_path,
    verify_sha256,
)
from packaging.security import PackageClass, PackageManifest, evaluate_default, verify_package


class SecurityBoundaryTests(unittest.TestCase):
    def test_paths_and_filenames_are_platform_safe_and_collision_safe(self) -> None:
        for value in ("../x", "..\\x", "C:\\x", "\\\\server\\share\\x", "CON.txt", "a/b."):
            with self.assertRaises(SecurityBoundaryError):
                validate_relative_path(value)
        self.assertEqual(validate_relative_path("字幕/片段.mp4"), "字幕/片段.mp4")
        used: set[str] = set()
        self.assertEqual(allocate_filename("clip.mp4", used), "clip.mp4")
        self.assertEqual(allocate_filename("CLIP.mp4", used), "CLIP (1).mp4")

    def test_archive_plan_rejects_zip_slip_links_duplicates_and_size_bombs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                plan_archive_extract(root, [ArchiveMember("ok/file.bin", size_bytes=4)])[0],
                root / "ok" / "file.bin",
            )
            for path in ("../escape", "C:\\escape", "\\\\server\\share\\x"):
                with self.assertRaises(SecurityBoundaryError):
                    plan_archive_extract(root, [ArchiveMember(path)])
            with self.assertRaises(SecurityBoundaryError):
                plan_archive_extract(root, [ArchiveMember("link", kind="symlink")])
            with self.assertRaises(SecurityBoundaryError):
                plan_archive_extract(root, [ArchiveMember("A", size_bytes=1), ArchiveMember("a", size_bytes=1)])

    def test_subprocess_and_diagnostics_do_not_interpolate_or_leak_credentials(self) -> None:
        plan = build_subprocess_plan("ffmpeg", ["input; echo pwned", "$(whoami)"])
        self.assertFalse(plan.shell)
        self.assertEqual(plan.argv[1], "input; echo pwned")
        redacted = redact_diagnostics(
            {"Cookie": "sid=secret", "nested": ["Authorization: Bearer bearer-secret", "safe"]}
        )
        self.assertNotIn("secret", repr(redacted))
        self.assertEqual(redacted["nested"][1], "safe")

    def test_package_eligibility_requires_supply_chain_approval_for_code(self) -> None:
        base = {
            "id": "runtime",
            "version": "1.0.0",
            "source": "vendor://runtime",
            "sha256": "a" * 64,
            "size_bytes": 10,
            "class": "code-bearing",
            "license": "MIT",
            "redistributable": True,
        }
        rejected = PackageManifest.from_mapping(base)
        self.assertFalse(evaluate_default(rejected).eligible)
        accepted = PackageManifest.from_mapping({**base, "signed": True, "code_audit_id": "audit-1"})
        self.assertTrue(evaluate_default(accepted).eligible)
        weights = PackageManifest.from_mapping({**base, "class": PackageClass.WEIGHTS_ONLY.value, "signed": False})
        self.assertTrue(evaluate_default(weights).eligible)
        with self.assertRaises(SecurityBoundaryError):
            PackageManifest.from_mapping({**base, "size_bytes": 2**64})

    def test_package_hash_and_size_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pack.bin"
            path.write_bytes(b"fixture")
            digest = hashlib.sha256(b"fixture").hexdigest()
            manifest = PackageManifest(
                package_id="weights",
                version="1",
                source="fixture://weights",
                sha256=digest,
                size_bytes=7,
                package_class=PackageClass.WEIGHTS_ONLY,
                license_id="CC-BY",
                redistributable=True,
            )
            self.assertTrue(verify_package(path, manifest))
            self.assertTrue(verify_sha256(path, digest, expected_size=7))
            self.assertFalse(verify_sha256(path, digest, expected_size=8))


if __name__ == "__main__":
    unittest.main()
