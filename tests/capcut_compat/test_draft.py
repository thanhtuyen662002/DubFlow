from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from engine.dubflow.capcut.draft import DraftStatus, DirectDraftError, FixtureDraftBackend, VersionedDraftAdapter
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
