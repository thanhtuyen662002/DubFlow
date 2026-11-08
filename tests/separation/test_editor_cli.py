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

    def test_a_failed_music_split_is_a_partial_success_that_keeps_the_voice(self):
        with TemporaryDirectory() as temp:
            out, source = Path(temp) / "stems", Path(temp) / "source_audio.wav"
            source.write_bytes(b"RIFF")

            def separate(source_wav, background_wav, **options):
                Path(background_wav).write_bytes(b"not a wav")
                return {}
            status = cli.Status(out / "status.json")
            result = cli.run(source, out, app_root=REPO, model_root=Path(temp) / "models", ffmpeg=Path("ffmpeg"),
                             ffprobe=Path("ffprobe"), status=status, separate=separate, ensure=lambda *a, **k: None,
                             extract=lambda *a, **k: None)
            saved = _status(out)
            self.assertEqual(result["state"], "COMPLETED")
            self.assertTrue(saved["split_error"])
            self.assertIn("chưa tách được nhạc và tiếng động", saved["message"])
            self.assertNotEqual(saved["message"], "Đã tách giọng, nhạc và tiếng động.")
            # The usable accompaniment stays; no half-made parts are left.
            self.assertEqual((out / "background.wav").read_bytes(), b"not a wav")
            self.assertFalse((out / "music.wav").exists() or (out / "sfx.wav").exists())

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


    def test_a_linked_out_folder_gets_no_status_or_output(self):
        from tests.separation.test_music_split import link_dir, snapshot

        with TemporaryDirectory() as temp:
            outside = Path(temp) / "outside"
            outside.mkdir()
            (outside / "status.json").write_text("keep", encoding="utf-8")
            before = snapshot(outside)
            out = Path(temp) / "stems"
            link_dir(outside, out)
            source = Path(temp) / "a.wav"
            source.write_bytes(b"RIFF")
            with patch.object(cli, "run") as run:
                code = cli.main(["--source", str(source), "--out-dir", str(out), "--app-root", str(REPO), "--model-root", temp,
                                 "--ffmpeg", "ffmpeg", "--ffprobe", "ffprobe"])
            self.assertEqual(code, 1)
            run.assert_not_called()
            self.assertEqual(snapshot(outside), before)


    def test_a_plain_video_is_read_through_the_trusted_media_folder(self):
        """A video (not a WAV) is probed and extracted with the app's media folder as the trusted root;
        without one the adapter refuses (EXECUTABLE_ROOT_REQUIRED), which broke Tách giọng on plain videos."""
        from engine.dubflow.media import adapter

        with TemporaryDirectory() as temp:
            media = Path(temp) / "runtime" / "media"
            media.mkdir(parents=True)
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (media / name).write_bytes(b"x")
            seen = {}

            class Probe:
                def __init__(self, path, **kwargs):
                    seen["probe"] = kwargs.get("trusted_root")

                def probe(self, source):
                    class Result:
                        has_audio = True
                        video = None
                    return Result()

            class Media:
                def __init__(self, path, **kwargs):
                    seen["ffmpeg"] = kwargs.get("trusted_root")

                def extract_audio(self, *args, **kwargs):
                    seen["extracted"] = True
            with patch.object(adapter, "MediaProbe", Probe), patch.object(adapter, "FfmpegMediaAdapter", Media):
                cli._extract(Path(temp) / "a.mp4", Path(temp) / "source.wav", ffmpeg=media / "ffmpeg.exe", ffprobe=media / "ffprobe.exe")
            self.assertEqual(seen, {"probe": media, "ffmpeg": media, "extracted": True})
        with self.assertRaises(adapter.MediaAdapterError):
            adapter.MediaProbe(Path(temp) / "ffprobe.exe")


if __name__ == "__main__":
    unittest.main()
