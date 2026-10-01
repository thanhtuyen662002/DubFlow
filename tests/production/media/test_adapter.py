from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from engine.dubflow.media import (
    FfmpegMediaAdapter,
    MediaAdapterError,
    MediaProbe,
    Rational,
    parse_ffprobe_json,
)


def _probe_document() -> dict[str, object]:
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 640,
                "height": 360,
                "time_base": "1/12800",
                "start_pts": -12,
                "duration_ts": 512,
                "r_frame_rate": "25/1",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "time_base": "1/48000",
                "start_pts": 0,
                "duration": "0.040000",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "time_base": "1/1000",
            "duration": "0.040000",
        },
    }


class RecordingRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail

    def __call__(self, argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "decoder failed token=should-not-leak")
        if "-show_streams" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_probe_document()), "")
        Path(argv[-1]).write_bytes(b"real-media-command-output")
        return subprocess.CompletedProcess(argv, 0, "", "")


class MediaProbeParsingTests(unittest.TestCase):
    def test_parse_preserves_integer_timebase_pts_and_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            result = parse_ffprobe_json(json.dumps(_probe_document()), source_path=source.resolve())

        self.assertEqual(result.video.time_base, Rational(1, 12800))
        self.assertEqual(result.video.start_pts, -12)
        self.assertEqual(result.video.duration_ticks, 512)
        self.assertEqual(result.video.frame_rate, Rational(25, 1))
        self.assertTrue(result.has_audio)
        self.assertEqual(result.audio[0].sample_rate, 48000)
        self.assertEqual(result.audio[0].duration_ticks, 1920)
        self.assertEqual(result.duration_ticks, 40)
        serialized = result.to_dict()
        self.assertEqual(serialized["canonical_duration_ticks"], 40)
        self.assertEqual(serialized["video_timeline"]["start_pts"], -1)
        self.assertEqual(serialized["video_timeline"]["duration_ticks"], 40)
        self.assertEqual(serialized["video_timeline"]["end_pts"], 39)
        timeline = result.video_timeline()
        self.assertEqual(timeline.start_pts, -12)
        self.assertEqual(timeline.duration_ticks, 512)
        self.assertEqual(timeline.end_pts, 500)
        self.assertEqual(result.video_timeline(Rational(1, 25600)).start_pts, -24)
        self.assertEqual(result.video_timeline(Rational(1, 25600)).duration_ticks, 1024)

    def test_non_integral_rescale_requires_named_rounding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            result = parse_ffprobe_json(json.dumps(_probe_document()), source_path=source.resolve())
        with self.assertRaisesRegex(MediaAdapterError, "MEDIA_TIMELINE_NON_INTEGRAL"):
            result.video_timeline(Rational(1, 1000))
        rounded = result.video_timeline(Rational(1, 1000), start_rounding="floor")
        self.assertEqual(rounded.start_pts, -1)
        self.assertEqual(rounded.duration_ticks, 40)
        self.assertEqual(rounded.end_pts, 39)

    def test_decimal_duration_rounds_up_without_binary_float(self) -> None:
        document = _probe_document()
        streams = document["streams"]
        assert isinstance(streams, list)
        streams[0].pop("duration_ts")
        streams[0]["duration"] = "0.0400001"
        with tempfile.TemporaryDirectory() as directory:
            result = parse_ffprobe_json(json.dumps(document), source_path=(Path(directory) / "x.mp4").resolve())
        self.assertEqual(result.video.duration_ticks, 513)

    def test_format_decimal_duration_without_ffprobe_timebase_uses_canonical_base(self) -> None:
        document = _probe_document()
        streams = document["streams"]
        assert isinstance(streams, list)
        streams[0].pop("duration_ts")
        format_document = document["format"]
        assert isinstance(format_document, dict)
        format_document.pop("time_base")
        format_document["duration"] = "0.0400001"
        with tempfile.TemporaryDirectory() as directory:
            result = parse_ffprobe_json(json.dumps(document), source_path=(Path(directory) / "x.mp4").resolve())
        self.assertEqual(result.format_time_base, Rational(1, 1000))
        self.assertEqual(result.format_duration_ticks, 41)
        self.assertEqual(result.duration_ticks, 41)

    def test_invalid_json_and_missing_video_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = (Path(directory) / "x.mp4").resolve()
            with self.assertRaisesRegex(MediaAdapterError, "MEDIA_METADATA_INVALID"):
                parse_ffprobe_json("{broken", source_path=source)
            no_video = {"streams": [{"index": 0, "codec_type": "audio", "time_base": "1/48000"}], "format": {}}
            with self.assertRaisesRegex(MediaAdapterError, "VIDEO_STREAM_MISSING"):
                parse_ffprobe_json(json.dumps(no_video), source_path=source)


class MediaCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dubflow media [space]-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source [input].mp4"
        self.source.write_bytes(b"source")
        self.audio = self.root / "voice track.wav"
        self.audio.write_bytes(b"audio")
        self.subtitle = self.root / "captions [vi].ass"
        self.subtitle.write_text("[Script Info]\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_probe_and_operations_use_absolute_argv_without_shell(self) -> None:
        probe_runner = RecordingRunner()
        trusted_root = Path(sys.executable).resolve().parent
        probe = MediaProbe(sys.executable, trusted_root=trusted_root, runner=probe_runner)
        result = probe.probe(self.source)
        self.assertEqual(result.video.width, 640)
        self.assertEqual(probe_runner.calls[0][0], sys.executable)
        self.assertIn(str(self.source.resolve()), probe_runner.calls[0])
        self.assertNotIn(" ".join(("-i", str(self.source))), probe_runner.calls[0])

        ffmpeg_runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=trusted_root, runner=ffmpeg_runner)
        extracted = adapter.extract_audio(self.source, self.root / "audio out.wav")
        muxed = adapter.mux_audio(self.source, extracted, self.root / "muxed output.mp4")
        rendered = adapter.render(
            self.source,
            self.root / "rendered [output].mp4",
            subtitle_path=self.subtitle,
            audio_path=extracted,
            preserve_original_audio=False,
        )

        self.assertTrue(extracted.is_file())
        self.assertTrue(muxed.is_file())
        self.assertTrue(rendered.is_file())
        self.assertTrue(all(call[0] == sys.executable for call in ffmpeg_runner.calls))
        render_call = ffmpeg_runner.calls[-1]
        self.assertIn(str(self.subtitle.resolve()), render_call)
        self.assertIn(str(extracted.resolve()), render_call)
        self.assertIn("-c:v", render_call)
        self.assertIn("h264_mf", render_call)
        self.assertIn("-c:a", render_call)
        self.assertNotIn("|", " ".join(render_call))
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_burn_in_escapes_filter_path_without_shelling(self) -> None:
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        output = adapter.render(
            self.source,
            self.root / "burned.mp4",
            subtitle_path=self.subtitle,
            preserve_original_audio=False,
            burn_in_subtitles=True,
        )
        self.assertTrue(output.is_file())
        call = runner.calls[-1]
        filter_value = call[call.index("-vf") + 1]
        self.assertIn("subtitles=", filter_value)
        self.assertTrue(filter_value.split("=", 1)[1].startswith(".dubflow-subtitle-"))
        self.assertTrue(filter_value.endswith(".ass"))
        self.assertNotIn(str(self.subtitle.resolve()), call)

    def test_visual_masks_use_bounded_argv_filter_and_preserve_audio(self) -> None:
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        output = adapter.apply_visual_masks(
            self.source,
            self.root / "cleaned.mp4",
            [{"x": 10, "y": 20, "width": 100, "height": 30, "start_ticks": 0, "end_ticks": 1250}],
        )
        self.assertTrue(output.is_file())
        call = runner.calls[-1]
        filter_value = call[call.index("-vf") + 1]
        self.assertIn("delogo=x=10:y=20:w=100:h=30", filter_value)
        self.assertIn("between(t,0,1.25)", filter_value)
        self.assertIn("0:a:0?", call)
        self.assertIn("h264_mf", call)

    def test_visual_masks_reject_malformed_intervals(self) -> None:
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=RecordingRunner())
        with self.assertRaisesRegex(MediaAdapterError, "MEDIA_MASK_INVALID"):
            adapter.apply_visual_masks(
                self.source,
                self.root / "bad.mp4",
                [{"x": 0, "y": 0, "width": 10, "height": 10, "start_ticks": 100, "end_ticks": 100}],
            )

    def test_frame_sampling_publishes_non_empty_atomic_directory(self) -> None:
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        frame_dir = self.root / "frames"
        frames = adapter.sample_frames(self.source, frame_dir, interval_ms=1000, max_frames=4)
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].is_file())
        call = runner.calls[-1]
        self.assertIn("fps=1000/1000", call)
        self.assertIn("-frames:v", call)
        self.assertFalse(list(self.root.glob(".frames.*.partial")))

    def test_failed_command_removes_partial_and_redacts_diagnostic(self) -> None:
        runner = RecordingRunner(fail=True)
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        output = self.root / "failed.mp4"
        with self.assertRaises(MediaAdapterError) as context:
            adapter.render(self.source, output, preserve_original_audio=False)
        self.assertEqual(context.exception.code, "MEDIA_COMMAND_FAILED")
        self.assertNotIn("should-not-leak", context.exception.condition)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_rejects_path_lookup_and_existing_output_without_overwrite(self) -> None:
        with self.assertRaisesRegex(MediaAdapterError, "EXECUTABLE_NOT_APP_OWNED"):
            MediaProbe("ffprobe")
        with self.assertRaisesRegex(MediaAdapterError, "EXECUTABLE_ROOT_REQUIRED"):
            MediaProbe(sys.executable)
        with self.assertRaisesRegex(MediaAdapterError, "EXECUTABLE_OUTSIDE_ROOT"):
            MediaProbe(sys.executable, trusted_root=self.root)
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        output = self.root / "existing.mp4"
        output.write_bytes(b"keep")
        with self.assertRaisesRegex(MediaAdapterError, "OUTPUT_EXISTS"):
            adapter.render(self.source, output, preserve_original_audio=False)
        self.assertEqual(output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
