from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from engine.dubflow.capcut.import_pack import ImportPackError, ImportPackRequest, create_import_pack, validate_import_pack


class ImportPackTests(unittest.TestCase):
    def test_portable_manifest_and_track_order_with_optional_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "源 video.mp4"
            subtitle = root / "vietsub.srt"
            dub = root / "dub.wav"
            source.write_bytes(b"video")
            subtitle.write_text("1\\n00:00:00,000 --> 00:00:01,000\\nXin chao\\n", encoding="utf-8")
            dub.write_bytes(b"audio")
            pack = create_import_pack(ImportPackRequest("job-1", source, subtitle, dub), root / "pack")
            manifest = validate_import_pack(pack.root)
            self.assertEqual([track["kind"] for track in manifest["tracks"]], ["video", "audio", "subtitle"])
            self.assertTrue(all(not Path(asset["relative_path"]).is_absolute() for asset in manifest["assets"]))
            moved = root / "moved" / "pack"
            moved.parent.mkdir()
            pack.root.rename(moved)
            self.assertEqual(validate_import_pack(moved)["pack_id"], "job-1")

    def test_missing_optional_stems_are_not_faked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            manifest = validate_import_pack(create_import_pack(ImportPackRequest("subtitle-only", source), root / "pack").root)
            self.assertEqual(len(manifest["assets"]), 1)
            self.assertEqual([track["kind"] for track in manifest["tracks"]], ["video"])

    def test_partial_or_existing_output_never_replaces_final_pack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            output = root / "pack"
            output.mkdir()
            with self.assertRaisesRegex(ImportPackError, "OUTPUT_EXISTS"):
                create_import_pack(ImportPackRequest("job-1", source), output)
            self.assertFalse(list(root.glob(".*.partial-*")))


if __name__ == "__main__":
    unittest.main()
