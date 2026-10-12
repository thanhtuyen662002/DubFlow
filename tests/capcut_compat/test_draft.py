from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from engine.dubflow.capcut.draft import DraftStatus, DirectDraftError, FixtureDraftBackend, VersionedDraftAdapter
from engine.dubflow.capcut.draft.adapter import CapCutVersion
from engine.dubflow.capcut.import_pack import ImportPackRequest, create_import_pack


class DraftAdapterTests(unittest.TestCase):
    def make_pack(self, root: Path) -> Path:
        source = root / "source.mp4"
        source.write_bytes(b"video")
        return create_import_pack(ImportPackRequest("job", source), root / "pack").root

    def test_exact_supported_version_generates_validated_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            result = VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="4.2.0", pack_root=pack, controlled_root=root / "controlled", backend=FixtureDraftBackend())
            self.assertEqual(result.status, DraftStatus.DIRECT)
            self.assertTrue(result.path and result.path.is_file())

    def test_unknown_version_falls_back_without_writing_capcut_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            result = VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="5.0.0", pack_root=pack, controlled_root=root / "controlled", backend=FixtureDraftBackend())
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertEqual(result.reason, "CAPCUT_VERSION_UNSUPPORTED")
            self.assertFalse((root / "controlled").exists())

    def test_four_component_build_identity_is_preserved(self) -> None:
        # Fixture admission checks version transport only, not vendor compatibility.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            version = "9.5.0.4050"
            result = VersionedDraftAdapter({version}).generate_or_fallback(
                installed_version=version, pack_root=pack,
                controlled_root=root / "controlled with spaces", backend=FixtureDraftBackend(),
            )
            self.assertEqual(result.status, DraftStatus.DIRECT)
            self.assertEqual(result.path.name, "dubflow-9.5.0.4050.draft.json")
            self.assertEqual(json.loads(result.path.read_text(encoding="utf-8"))["capcut_version"], version)

    def test_buildless_and_neighbor_builds_cannot_authorize_each_other(self) -> None:
        for supported, installed in (
            ("9.5.0", "9.5.0.4050"),
            ("9.5.0.4050", "9.5.0"),
            ("9.5.0.4050", "9.5.0.4051"),
        ):
            with self.subTest(supported=supported, installed=installed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pack = self.make_pack(root)
                result = VersionedDraftAdapter({supported}).generate_or_fallback(
                    installed_version=installed, pack_root=pack,
                    controlled_root=root / "controlled", backend=FixtureDraftBackend(),
                )
                self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
                self.assertEqual(result.reason, "CAPCUT_VERSION_UNSUPPORTED")
                self.assertFalse((root / "controlled").exists())

    def test_backend_cannot_truncate_tested_build_in_output(self) -> None:
        class TruncatingBackend(FixtureDraftBackend):
            def generate(self, *, version, pack_root, output_path):
                super().generate(version=CapCutVersion.parse("9.5.0"), pack_root=pack_root, output_path=output_path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            result = VersionedDraftAdapter({"9.5.0.4050"}).generate_or_fallback(
                installed_version="9.5.0.4050", pack_root=pack,
                controlled_root=root / "controlled", backend=TruncatingBackend(),
            )
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertIn("tested version", result.reason)
            self.assertEqual(list((root / "controlled").iterdir()), [])

    def test_version_comparison_retains_build_identity_and_legacy_ordering(self) -> None:
        versions = [CapCutVersion.parse(v) for v in ("9.5.0.4051", "9.5.0", "9.5.0.0", "9.5.0.4050", "4.2.0")]
        self.assertEqual(len(set(versions)), 5)
        self.assertEqual([str(v) for v in sorted(versions)], ["4.2.0", "9.5.0", "9.5.0.0", "9.5.0.4050", "9.5.0.4051"])
        for value in ("9.5", "9.5.0.4050.1", "9.5.0.04050", "9.5.0.-1", "9.5.0.4050\n", "9.5.0." + "1" * 11):
            with self.subTest(value=value), self.assertRaises(DirectDraftError):
                CapCutVersion.parse(value)

    def test_backend_failure_falls_back_and_cleans_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            controlled = root / "controlled"
            result = VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="4.2.0", pack_root=pack, controlled_root=controlled, backend=FixtureDraftBackend(fail=True))
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertEqual(list(controlled.glob("*.partial")), [])

    def test_missing_pack_is_a_scoped_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DirectDraftError, "validated import pack"):
                VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="4.2.0", pack_root=Path(directory) / "missing", controlled_root=Path(directory) / "controlled", backend=FixtureDraftBackend())


if __name__ == "__main__":
    unittest.main()
