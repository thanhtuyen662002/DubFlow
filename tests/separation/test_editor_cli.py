"""Tách giọng in the editor: `python -m engine.dubflow.separation` writes a readable status."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.separation import __main__ as cli
from engine.dubflow.separation.production import SeparationError

REPO = Path(__file__).resolve().parents[2]


def _status(out: Path) -> dict:
    return json.loads((out / "status.json").read_text(encoding="utf-8"))


class EditorSeparationTest(unittest.TestCase):
    def test_wav_source_is_separated_in_place(self):
        with TemporaryDirectory() as temp:
            out, source = Path(temp) / "stems", Path(temp) / "source_audio.wav"
            source.write_bytes(b"RIFF")
            calls = []

            def separate(source_wav, background_wav, **options):
                calls.append((source_wav, background_wav, options["model_dir"]))
                options["progress"](0.5)
                Path(background_wav).write_bytes(b"bg")
                return {}
            status = cli.Status(out / "status.json")
            result = cli.run(source, out, app_root=REPO, model_root=Path(temp) / "models", ffmpeg=Path("ffmpeg"),
                             ffprobe=Path("ffprobe"), status=status, separate=separate, ensure=lambda *a, **k: None,
                             extract=lambda *a, **k: self.fail("a WAV is not extracted again"))
            self.assertEqual(result["state"], "COMPLETED")
            self.assertEqual(_status(out)["progress"], 100)
            self.assertEqual(calls[0][0], source)
            self.assertEqual(calls[0][1], out / "background.wav")
            self.assertTrue(str(calls[0][2]).endswith("uvr-mdx-kim-vocal-2-v1"))

    def test_video_source_is_extracted_first(self):
        with TemporaryDirectory() as temp:
            out, video = Path(temp) / "stems", Path(temp) / "clip.mp4"
            video.write_bytes(b"mp4")
            seen = []

            def extract(source, target, **_):
                seen.append((source, target))
                Path(target).write_bytes(b"wav")
            status = cli.Status(out / "status.json")
            cli.run(video, out, app_root=REPO, model_root=Path(temp), ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"),
                    status=status, separate=lambda source_wav, *_a, **_k: seen.append(source_wav), ensure=lambda *a, **k: None,
                    extract=extract)
            self.assertEqual(seen, [(video, out / "source.wav"), out / "source.wav"])

    def test_cancel_file_stops_the_run(self):
        with TemporaryDirectory() as temp:
            out = Path(temp) / "stems"
            out.mkdir()
            source = Path(temp) / "a.wav"
            source.write_bytes(b"RIFF")

            def separate(_source, _background, **options):
                (out / "cancel").write_text("1")
                options["progress"](0.2)
            result = cli.run(source, out, app_root=REPO, model_root=Path(temp), ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"),
                             status=cli.Status(out / "status.json"), separate=separate, ensure=lambda *a, **k: None)
            self.assertEqual(result["state"], "CANCELLED")

    def test_failure_is_a_vietnamese_status(self):
        with TemporaryDirectory() as temp:
            out = Path(temp) / "stems"
            source = Path(temp) / "a.wav"
            source.write_bytes(b"RIFF")
            with patch.object(cli, "run", side_effect=SeparationError("SEPARATION_REJECTED", "clips")):
                code = cli.main(["--source", str(source), "--out-dir", str(out), "--app-root", str(REPO), "--model-root", temp,
                                 "--ffmpeg", "ffmpeg", "--ffprobe", "ffprobe"])
            self.assertEqual(code, 1)
            self.assertEqual(_status(out)["state"], "FAILED")
            self.assertIn("không dùng được", _status(out)["message"])


if __name__ == "__main__":
    unittest.main()
