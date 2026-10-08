from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from engine.dubflow.capcut.production import create_production_pack, detect_capcut_version


class ProductionImportPackTests(unittest.TestCase):
    def test_production_pack_contains_timeline_and_survives_move(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubflow-pack-") as directory:
            root = Path(directory)
            source = root / "source with spaces.mp4"
            subtitle = root / "captions.srt"
            audio = root / "dub.wav"
            source.write_bytes(b"video")
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chao\n", encoding="utf-8")
            audio.write_bytes(b"audio")
            result = create_production_pack(
                pack_id="job-172",
                source_path=source,
                output_root=root / "exports" / "pack",
                subtitle_path=subtitle,
                dub_audio_path=audio,
                timeline={
                    "time_base": {"numerator": 1, "denominator": 1000},
                    "duration_ticks": 1000,
                    "cues": [{"cue_id": "cue-1", "start_ticks": 0, "end_ticks": 1000, "voice_id": "vi-builtin-v1"}],
                },
            )
            self.assertTrue(result.pack.root.is_dir())
            self.assertTrue((result.pack.root / "manifest.json").is_file())
            self.assertEqual(result.pack.manifest["timeline"]["duration_ticks"], 1000)
            moved = root / "moved" / "pack"
            moved.parent.mkdir()
            result.pack.root.rename(moved)
            self.assertTrue((moved / "media" / source.name).is_file())

    def test_direct_handoff_is_scoped_and_unknown_version_falls_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubflow-pack-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            result = create_production_pack(
                pack_id="job-172-fallback",
                source_path=source,
                output_root=root / "pack",
                installed_capcut_version="99.0.0",
                supported_capcut_versions={"4.2.0"},
                controlled_capcut_root=root / "controlled",
            )
            self.assertIsNotNone(result.direct_draft)
            assert result.direct_draft is not None
            self.assertEqual(result.direct_draft.status.value, "FALLBACK_IMPORT_PACK")
            self.assertFalse((root / "controlled").exists())
            self.assertTrue(result.pack.root.is_dir())

    def test_capcut_detection_never_infers_blank_or_arbitrary_path(self) -> None:
        self.assertIsNone(detect_capcut_version({}))
        self.assertIsNone(detect_capcut_version({"DUBFLOW_CAPCUT_VERSION": "  "}))
        self.assertEqual(detect_capcut_version({"DUBFLOW_CAPCUT_VERSION": "4.2.0"}), "4.2.0")


if __name__ == "__main__":
    unittest.main()
