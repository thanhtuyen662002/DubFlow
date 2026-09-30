from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.worker.production_job import (
    ProductionJobError,
    TextCue,
    WorkerConfig,
    _load_sidecar,
    _validate_rendered_audio,
    _write_subtitles,
)
from engine.dubflow.media import parse_ffprobe_json


class ProductionWorkerTests(unittest.TestCase):
    def test_srt_and_vtt_sidecars_preserve_integer_millisecond_ticks(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-sidecar-") as directory:
            root = Path(directory)
            srt_video = root / "episode [spaces].mp4"
            srt_video.write_bytes(b"media")
            srt_video.with_suffix(".srt").write_text(
                "1\n00:00:01,005 --> 00:00:02,250\nHello\n",
                encoding="utf-8",
            )
            srt = _load_sidecar(srt_video)
            self.assertIsNotNone(srt)
            self.assertEqual((srt[0].start_ms, srt[0].end_ms), (1005, 2250))

            vtt_video = root / "clip.mp4"
            vtt_video.write_bytes(b"media")
            vtt_video.with_suffix(".vtt").write_text(
                "WEBVTT\n\n00:01.500 --> 00:02.750\nXin chao\n",
                encoding="utf-8",
            )
            vtt = _load_sidecar(vtt_video)
            self.assertIsNotNone(vtt)
            self.assertEqual((vtt[0].start_ms, vtt[0].end_ms), (1500, 2750))

    def test_subtitle_outputs_are_atomic_and_editable(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-subtitle-") as directory:
            root = Path(directory)
            srt, ass = _write_subtitles(
                root,
                (TextCue("cue-1", 0, 1250, "Hello", "Xin chào"),),
            )
            self.assertEqual(srt.read_text(encoding="utf-8").splitlines()[2], "Xin chào")
            self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.25", ass.read_text(encoding="utf-8"))
            self.assertFalse(list(root.glob("*.partial")))

    def test_worker_rejects_system_media_executable(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-security-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            app = root / "app"
            app.mkdir()
            media = root / "media"
            media.mkdir()
            ffmpeg = media / "ffmpeg.exe"
            ffprobe = media / "ffprobe.exe"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            with self.assertRaisesRegex(ProductionJobError, "RUNTIME_ROOT_UNSAFE"):
                WorkerConfig.from_args(
                    {
                        "job_id": "job-1",
                        "stage_id": "local-file",
                        "source_path": str(source),
                        "output_dir": str(root / "output"),
                        "app_root": str(app),
                        "model_root": str(root / "models"),
                        "media_runtime_root": str(media),
                        "ffmpeg_path": str(root / "outside-ffmpeg.exe"),
                        "ffprobe_path": str(ffprobe),
                    }
                )

    def test_qc_rejects_render_that_drops_source_audio(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-qc-") as directory:
            source_path = Path(directory) / "source.mp4"
            source_path.write_bytes(b"media")
            source_probe = parse_ffprobe_json(
                json.dumps({
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "codec_name": "h264",
                            "time_base": "1/1000",
                            "start_pts": 0,
                            "duration_ts": 1000,
                        },
                        {
                            "index": 1,
                            "codec_type": "audio",
                            "codec_name": "aac",
                            "time_base": "1/48000",
                            "start_pts": 0,
                            "duration_ts": 48000,
                        },
                    ],
                    "format": {"duration_ts": 1000, "time_base": "1/1000"},
                }),
                source_path=source_path,
            )
            output_probe = parse_ffprobe_json(
                json.dumps({
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "codec_name": "h264",
                            "time_base": "1/1000",
                            "start_pts": 0,
                            "duration_ts": 1000,
                        }
                    ],
                    "format": {"duration_ts": 1000, "time_base": "1/1000"},
                }),
                source_path=Path(directory) / "output.mp4",
            )
            with self.assertRaisesRegex(ProductionJobError, "QC_AUDIO_MISSING"):
                _validate_rendered_audio(source_probe, output_probe)


if __name__ == "__main__":
    unittest.main()
